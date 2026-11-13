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


@dataclass(frozen=True)
class Correction:
    id: str
    order_pattern: str
    field: str
    wrong: object
    right: object
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


def load_corrections(path: str | Path = DEFAULT_PATH) -> list[Correction]:
    out: list[Correction] = []
    seen: set[str] = set()
    text = Path(path).read_text(encoding="utf-8")
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{n}: invalid JSON ({exc.msg})") from None
        missing = [k for k in REQUIRED_KEYS if k not in raw]
        extra = sorted(set(raw) - set(REQUIRED_KEYS))
        if missing or extra:
            raise ValueError(f"{path}:{n}: missing keys {missing}, unknown keys {extra}")
        if raw["id"] in seen:
            raise ValueError(f"{path}:{n}: duplicate id {raw['id']!r}")
        seen.add(raw["id"])
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


def compile_corrections(corrections: list[Correction]) -> list[ReplayCheck]:
    return [ReplayCheck(c, _field_regex(c.field)) for c in corrections]


def replay_run(
    db_path: str | Path, record: dict, checks: list[ReplayCheck]
) -> list[ReplayHit]:
    """Every correction that the ERP data for ``record`` repeats."""
    relevant = [c for c in checks if c.applies_to(record["order_id"])]
    if not relevant:
        return []
    stored = stored_fields(db_path, record.get("erp_order_id"))
    if stored is None:
        return []
    return [hit for check in relevant for hit in check.run(record, stored)]
