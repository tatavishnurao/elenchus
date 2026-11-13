"""Compare what the ERP stored for a run with the source order, field by field.

``diff_order`` returns a list of ``Mismatch`` objects. A run passes only when
that list is empty. Values are normalized before comparing: text ignores case
and extra whitespace, size names go through an alias table, and prices compare
as cents.
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
            "SELECT o.source_ref, o.notes, o.status, c.name, c.email, c.city "
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


def diff_order(
    db_path: str | Path, erp_order_id: int | None, source: dict
) -> list[Mismatch]:
    """Mismatches between ERP order ``erp_order_id`` and the ``source`` order."""
    loaded = _load_stored(db_path, erp_order_id)
    if loaded is None:
        return [Mismatch("order", "present", "missing")]
    row, lines = loaded

    out: list[Mismatch] = []

    def check(field: str, expected, actual, norm=norm_text) -> None:
        if norm(expected) != norm(actual):
            out.append(Mismatch(field, norm(expected), norm(actual)))

    customer = source["customer"]
    check("customer.name", customer["name"], row["name"])
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
    return out


def verify_run(db_path: str | Path, record: dict, orders_by_id: dict[str, dict]) -> dict:
    """Verdict for one run record. ``passed`` is True only with zero mismatches."""
    mismatches = diff_order(db_path, record["erp_order_id"], orders_by_id[record["order_id"]])
    return {
        "order_id": record["order_id"],
        "erp_order_id": record["erp_order_id"],
        "claimed_success": record["claimed_success"],
        "passed": not mismatches,
        "mismatches": [m.to_dict() for m in mismatches],
    }
