"""Compare what the ERP stored for a run with the source order, field by field.

``diff_result`` returns the ``Mismatch`` objects plus any ``Unverifiable``
fields. A run passes only when both are empty. Values are normalized before
comparing: text ignores case and extra whitespace, size names go through an
alias table, and prices compare as cents.

Source-minimal mode: when the source customer is only a name (no email or
city), the diff checks the name. If that name matches more than one ERP
customer it cannot tell which one the order was entered under, so it reports
the customer as ``Unverifiable`` instead of passing it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from ..erp.db import SIZE_COLUMNS, connect
from ..erp.seed import SIZES

SIZE_ALIASES = {
    "S": "S", "SM": "S", "SMALL": "S",
    "M": "M", "MD": "M", "MED": "M", "MEDIUM": "M",
    "L": "L", "LG": "L", "LARGE": "L",
    "XL": "XL", "X-LARGE": "XL", "XLARGE": "XL", "EXTRA LARGE": "XL",
    "2XL": "2XL", "XXL": "2XL", "2X": "2XL", "XX-LARGE": "2XL", "2X-LARGE": "2XL",
}


@dataclass(frozen=True)
class Mismatch:
    field: str
    expected: object
    actual: object

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Unverifiable:
    field: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DiffResult:
    mismatches: list[Mismatch]
    unverifiable: list[Unverifiable]

    @property
    def clean(self) -> bool:
        return not self.mismatches and not self.unverifiable


def is_minimal(source: dict) -> bool:
    """True when the source customer has no email or city to check against."""
    c = source["customer"]
    return "email" not in c or "city" not in c


def norm_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip().casefold()


def norm_size(value: str) -> str:
    key = re.sub(r"\s+", " ", str(value)).strip().upper()
    if key not in SIZE_ALIASES:
        raise ValueError(f"unknown size {value!r}")
    return SIZE_ALIASES[key]


def norm_sizes(sizes: dict) -> dict[str, int]:
    out = {size: 0 for size in SIZES}
    for name, qty in sizes.items():
        out[norm_size(name)] += int(qty)
    return out


def price_cents(value: object) -> int:
    return int(Decimal(str(value).strip()) * 100)


def _money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _load_stored(db_path: str | Path, erp_order_id: int | None):
    """(order row, line rows) for an ERP order, or None if it does not exist."""
    if erp_order_id is None:
        return None
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT o.source_ref, o.notes, o.status, o.customer_id, "
            "c.name, c.email, c.city, "
            "(SELECT COUNT(*) FROM customers c2 WHERE c2.name = c.name) AS same_name "
            "FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.id = ?",
            (erp_order_id,),
        ).fetchone()
        if row is None:
            return None
        lines = conn.execute(
            "SELECT * FROM order_lines WHERE order_id = ? ORDER BY line_no",
            (erp_order_id,),
        ).fetchall()
    finally:
        conn.close()
    return row, lines


def stored_fields(db_path: str | Path, erp_order_id: int | None) -> dict | None:
    """What the ERP stored, flattened to the same field names ``diff_order`` reports.

    Returns None when the order does not exist. Prices are strings like
    ``"41.50"``; quantities and sizes are ints.
    """
    loaded = _load_stored(db_path, erp_order_id)
    if loaded is None:
        return None
    row, lines = loaded
    out: dict[str, object] = {
        "customer.id": row["customer_id"],
        "customer.name": row["name"],
        "customer.city": row["city"],
        "customer.email": row["email"],
        "source_ref": row["source_ref"],
        "notes": row["notes"],
        "status": row["status"],
    }
    for n, line in enumerate(lines, start=1):
        p = f"lines[{n}]"
        out[f"{p}.sku"] = line["sku"]
        out[f"{p}.color"] = line["color"]
        for size in SIZES:
            out[f"{p}.sizes.{size}"] = line[SIZE_COLUMNS[size]]
        out[f"{p}.quantity"] = line["quantity"]
        out[f"{p}.unit_price"] = _money(line["unit_price_cents"])
    return out


def diff_result(
    db_path: str | Path,
    erp_order_id: int | None,
    source: dict,
    minimal: bool | None = None,
) -> DiffResult:
    """Compare ERP order ``erp_order_id`` with ``source``.

    ``minimal`` selects source-minimal mode; None detects it from the source.
    """
    loaded = _load_stored(db_path, erp_order_id)
    if loaded is None:
        return DiffResult([Mismatch("order", "present", "missing")], [])
    row, lines = loaded
    if minimal is None:
        minimal = is_minimal(source)
    unverifiable: list[Unverifiable] = []

    out: list[Mismatch] = []

    def check(field: str, expected, actual, norm=norm_text) -> None:
        if norm(expected) != norm(actual):
            out.append(Mismatch(field, norm(expected), norm(actual)))

    customer = source["customer"]
    check("customer.name", customer["name"], row["name"])
    if minimal:
        if row["same_name"] > 1:
            unverifiable.append(
                Unverifiable(
                    "customer",
                    f"the source gives only the name {customer['name']!r}, which matches "
                    f"{row['same_name']} ERP customers, so the account cannot be checked",
                )
            )
    else:
        check("customer.city", customer["city"], row["city"])
        check("customer.email", customer["email"], row["email"])
    check("source_ref", source["source_ref"], row["source_ref"])
    check("notes", source["notes"], row["notes"])
    check("status", "submitted", row["status"])

    expected_lines = source["lines"]
    if len(expected_lines) != len(lines):
        out.append(Mismatch("lines.count", len(expected_lines), len(lines)))
    for n, (exp, got) in enumerate(zip(expected_lines, lines), start=1):
        p = f"lines[{n}]"
        check(f"{p}.sku", exp["sku"], got["sku"])
        check(f"{p}.color", exp["color"], got["color"])
        exp_sizes = norm_sizes(exp["sizes"])
        for size in SIZES:
            actual = got[SIZE_COLUMNS[size]]
            if exp_sizes[size] != actual:
                out.append(Mismatch(f"{p}.sizes.{size}", exp_sizes[size], actual))
        check(f"{p}.quantity", exp["quantity"], got["quantity"], norm=int)
        if price_cents(exp["unit_price"]) != got["unit_price_cents"]:
            out.append(
                Mismatch(
                    f"{p}.unit_price",
                    _money(price_cents(exp["unit_price"])),
                    _money(got["unit_price_cents"]),
                )
            )
    return DiffResult(out, unverifiable)


def diff_order(
    db_path: str | Path, erp_order_id: int | None, source: dict
) -> list[Mismatch]:
    """Mismatches for a full-mode source order (customer has email and city).

    Source-minimal orders can have unverifiable fields that this list cannot
    show, so they go through ``diff_result`` instead.
    """
    if is_minimal(source):
        raise ValueError("source-minimal order: use diff_result to see unverifiable fields")
    return diff_result(db_path, erp_order_id, source, minimal=False).mismatches
