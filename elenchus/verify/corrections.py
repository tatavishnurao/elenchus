"""Replay past human corrections as regression checks on new runs.

``data/corrections.jsonl`` holds one JSON object per line, each a mistake a
person once had to fix in the ERP:

    {"id": "C-001", "order_pattern": "W-1002", "field": "lines[1].quantity",
     "wrong": 8, "right": 7, "reason": "..."}

``order_pattern`` is a glob matched against the source order id. ``field``
uses the names ``diff_order`` reports; ``lines[*]`` matches any line. Each
entry compiles into a ``ReplayCheck``. A check hits when the ERP stored the
known-wrong value for a matching field, and the hit names the correction that
caught it.

A second kind of entry, ``"kind": "resolve_customer"``, maps a source customer
name plus storefront to an ERP customer id:

    {"id": "C-006", "kind": "resolve_customer", "name": "Northwind Outfitters",
     "storefront": "web-austin", "customer_id": 1, "reason": "..."}

In source-minimal mode the diff cannot tell same-name customers apart. A
mapping resolves that: the order's customer is then checked against the id, and
a different id is a replay hit.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from .diff import norm_text, price_cents, stored_fields

DEFAULT_PATH = Path("data/corrections.jsonl")
REQUIRED_KEYS = ("id", "order_pattern", "field", "wrong", "right", "reason")
RESOLVE_KIND = "resolve_customer"
RESOLVE_KEYS = ("id", "kind", "name", "storefront", "customer_id", "reason")


@dataclass(frozen=True)
class Correction:
    id: str
    order_pattern: str
    field: str
    wrong: object
    right: object
    reason: str


@dataclass(frozen=True)
class CustomerMapping:
    id: str
    name: str
    storefront: str
    customer_id: int
    reason: str


@dataclass(frozen=True)
class ReplayHit:
    correction_id: str
    order_id: str
    erp_order_id: int | None
    field: str
    wrong: object
    right: object
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def load_corrections(path: str | Path = DEFAULT_PATH) -> list[Correction | CustomerMapping]:
    out: list[Correction | CustomerMapping] = []
    seen: set[str] = set()
    text = Path(path).read_text(encoding="utf-8")
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{n}: invalid JSON ({exc.msg})") from None
        resolve = raw.get("kind") == RESOLVE_KIND
        keys = RESOLVE_KEYS if resolve else REQUIRED_KEYS
        missing = [k for k in keys if k not in raw]
        extra = sorted(set(raw) - set(keys))
        if missing or extra:
            raise ValueError(f"{path}:{n}: missing keys {missing}, unknown keys {extra}")
        if raw["id"] in seen:
            raise ValueError(f"{path}:{n}: duplicate id {raw['id']!r}")
        seen.add(raw["id"])
        if resolve:
            out.append(CustomerMapping(**{k: v for k, v in raw.items() if k != "kind"}))
        else:
            out.append(Correction(**raw))
    return out


def _normalize(field: str, value: object) -> object:
    """Compare values the way diff.py does: ints, cents, or folded text."""
    if field.endswith((".quantity", ".sizes")) or ".sizes." in field:
        return int(value)
    if field.endswith(".unit_price"):
        return price_cents(value)
    return norm_text(value)


def _field_regex(field: str) -> re.Pattern[str]:
    return re.compile(re.escape(field).replace(r"\[\*\]", r"\[\d+\]"))


@dataclass(frozen=True)
class ReplayCheck:
    """A correction compiled for matching against one run."""

    correction: Correction
    _field_re: re.Pattern[str]

    def applies_to(self, order_id: str) -> bool:
        return fnmatchcase(order_id, self.correction.order_pattern)

    def run(self, record: dict, stored: dict) -> list[ReplayHit]:
        c = self.correction
        if not self.applies_to(record["order_id"]):
            return []
        wrong = _normalize(c.field, c.wrong)
        return [
            ReplayHit(
                c.id, record["order_id"], record.get("erp_order_id"),
                name, c.wrong, c.right, c.reason,
            )
            for name, value in stored.items()
            if self._field_re.fullmatch(name) and _normalize(name, value) == wrong
        ]


@dataclass(frozen=True)
class MappingCheck:
    """A name + storefront -> customer id mapping compiled for matching."""

    mapping: CustomerMapping

    def applies_to(self, source: dict | None) -> bool:
        if not source:
            return False
        m = self.mapping
        return (
            norm_text(source["customer"]["name"]) == norm_text(m.name)
            and source.get("storefront") == m.storefront
        )

    def run(self, record: dict, stored: dict, source: dict | None) -> list[ReplayHit]:
        m = self.mapping
        if not self.applies_to(source) or stored["customer.id"] == m.customer_id:
            return []
        return [
            ReplayHit(
                m.id, record["order_id"], record.get("erp_order_id"),
                "customer.id", stored["customer.id"], m.customer_id, m.reason,
            )
        ]


Check = ReplayCheck | MappingCheck


def compile_corrections(corrections: list[Correction | CustomerMapping]) -> list[Check]:
    return [
        MappingCheck(c) if isinstance(c, CustomerMapping) else ReplayCheck(c, _field_regex(c.field))
        for c in corrections
    ]


def resolving_check(checks: list[Check], source: dict) -> MappingCheck | None:
    """The mapping that says which ERP customer this source order is for, if any."""
    return next((c for c in checks if isinstance(c, MappingCheck) and c.applies_to(source)), None)


def replay_run(
    db_path: str | Path,
    record: dict,
    checks: list[Check],
    source: dict | None = None,
) -> list[ReplayHit]:
    """Every correction that the ERP data for ``record`` repeats.

    ``source`` is the source order; customer mappings need it (they match on
    its name and storefront) and are skipped without it.
    """
    relevant = [
        c for c in checks
        if (c.applies_to(record["order_id"]) if isinstance(c, ReplayCheck) else c.applies_to(source))
    ]
    if not relevant:
        return []
    stored = stored_fields(db_path, record.get("erp_order_id"))
    if stored is None:
        return []
    return [
        hit
        for c in relevant
        for hit in (c.run(record, stored) if isinstance(c, ReplayCheck) else c.run(record, stored, source))
    ]
