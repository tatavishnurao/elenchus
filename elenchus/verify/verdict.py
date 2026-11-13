"""Verdict for one run: the field diff plus replayed corrections.

A run passes only if the diff finds no mismatches, no field is unverifiable,
and no replay check hits. ``caught_by`` lists which checks found a problem.
"""

from __future__ import annotations

from pathlib import Path

from .corrections import Check, replay_run, resolving_check
from .diff import diff_result


def verify_run(
    db_path: str | Path,
    record: dict,
    orders_by_id: dict[str, dict],
    checks: list[Check] | None = None,
    minimal: bool | None = None,
) -> dict:
    """Verdict for one run record.

    ``checks`` come from ``compile_corrections``; with none, replay does not run.
    ``minimal`` selects source-minimal mode (None detects it from the order).
    A customer mapping resolves an unverifiable customer: the diff stops
    reporting it, and replay reports a hit if the ERP customer id is wrong.
    """
    checks = checks or []
    source = orders_by_id[record["order_id"]]
    result = diff_result(db_path, record["erp_order_id"], source, minimal)
    hits = replay_run(db_path, record, checks, source)
    unverifiable = [
        u for u in result.unverifiable
        if not (u.field == "customer" and resolving_check(checks, source))
    ]
    caught_by = [
        name for name, found in (("diff", result.mismatches), ("replay", hits)) if found
    ]
    return {
        "order_id": record["order_id"],
        "erp_order_id": record["erp_order_id"],
        "claimed_success": record["claimed_success"],
        "passed": not (result.mismatches or hits or unverifiable),
        "diff_mismatches": [m.to_dict() for m in result.mismatches],
        "replay_hits": [h.to_dict() for h in hits],
        "unverifiable": [u.to_dict() for u in unverifiable],
        "caught_by": caught_by,
    }
