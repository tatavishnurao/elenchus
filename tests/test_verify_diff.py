"""diff.py catches each injected fault and raises no false alarms on clean runs."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from elenchus.agent.faults import (
    FAULT_TYPES,
    OFF_BY_ONE_QUANTITY,
    STALE_PRICE,
    SWAPPED_SIZES,
    WRONG_CUSTOMER,
    FaultInjector,
)
from elenchus.agent.scripted import run_order, run_orders
from elenchus.orders import generate_orders
from elenchus.verify.diff import (
    Mismatch,
    diff_order,
    norm_size,
    norm_sizes,
    norm_text,
    verify_run,
)

ORDERS = generate_orders()
BY_ID = {o["id"]: o for o in ORDERS}


def _pick(tag: str, n: int) -> list[dict]:
    return [o for o in ORDERS if tag in o["tags"]][:n]


def _clean_sample() -> list[dict]:
    """10 orders, covering duplicate SKUs and same-name customers."""
    chosen = _pick("duplicate_name_customer", 3) + _pick("duplicate_sku", 3)
    for o in ORDERS:
        if len(chosen) >= 10:
            break
        if o not in chosen:
            chosen.append(o)
    return chosen[:10]


# --- normalization ---------------------------------------------------------

def test_normalization():
    assert norm_text("  Heather   GREY ") == norm_text("heather grey")
    assert norm_size("xxl") == norm_size("2XL") == "2XL"
    assert norm_size(" extra large ") == "XL"
    assert norm_sizes({"XXL": 3, "s": 1})["2XL"] == 3
    with pytest.raises(ValueError):
        norm_size("XXXXL")


def test_aliased_source_sizes_do_not_mismatch(erp, tmp_path):
    base_url, db = erp
    order = ORDERS[0]
    (rec,) = run_orders(base_url, [order], runs_path=tmp_path / "runs.jsonl")
    aliased = {**order, "lines": [
        {**ln, "sizes": {("XXL" if k == "2XL" else k.lower()): v for k, v in ln["sizes"].items()}}
        for ln in order["lines"]
    ]}
    assert diff_order(db, rec["erp_order_id"], aliased) == []


def test_missing_order():
    assert diff_order("unused.db", None, ORDERS[0]) == [Mismatch("order", "present", "missing")]


# --- one test per fault type -----------------------------------------------

def _faulted_run(erp, tmp_path, kind: str, order: dict):
    base_url, db = erp
    runs = tmp_path / "runs.jsonl"
    injector = FaultInjector(1.0, types=(kind,))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            rec = run_order(page, base_url, order, runs_path=runs, injector=injector)
        finally:
            browser.close()
    return rec, verify_run(db, rec, BY_ID)


@pytest.mark.parametrize("kind", FAULT_TYPES)
def test_diff_catches_fault(erp, tmp_path, kind):
    order = (
        _pick("duplicate_name_customer", 1)[0] if kind == WRONG_CUSTOMER else ORDERS[1]
    )
    rec, verdict = _faulted_run(erp, tmp_path, kind, order)

    # The agent still claims success: the fault is recorded apart from the claim.
    assert rec["claimed_success"] is True
    assert rec["fault"]["type"] == kind
    assert verdict["passed"] is False
    fields = [m["field"] for m in verdict["mismatches"]]
    assert fields and all(f.startswith(rec["fault"]["field"]) for f in fields), fields

    if kind == OFF_BY_ONE_QUANTITY:
        (m,) = verdict["mismatches"]
        assert m["actual"] == m["expected"] + 1
    elif kind == SWAPPED_SIZES:
        assert len(fields) >= 2  # both swapped sizes differ
    elif kind == WRONG_CUSTOMER:
        assert {"customer.city", "customer.email"} <= set(fields)
        assert "customer.name" not in fields
    elif kind == STALE_PRICE:
        (m,) = verdict["mismatches"]
        assert float(m["actual"]) < float(m["expected"])


# --- no false alarms ---------------------------------------------------------

def test_clean_runs_have_zero_mismatches(erp, tmp_path):
    base_url, db = erp
    sample = _clean_sample()
    assert len(sample) == 10
    records = run_orders(base_url, sample, runs_path=tmp_path / "runs.jsonl")
    for rec in records:
        assert rec["fault"] is None and rec["claimed_success"] is True
        verdict = verify_run(db, rec, BY_ID)
        assert verdict["mismatches"] == [], (rec["order_id"], verdict["mismatches"])
        assert verdict["passed"] is True
