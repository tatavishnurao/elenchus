"""verify_run combines the diff and replay; source-minimal mode needs a mapping."""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from elenchus.agent.faults import STALE_PRICE, WRONG_CUSTOMER, FaultInjector
from elenchus.agent.scripted import run_order
from elenchus.orders import generate_orders
from elenchus.verify.corrections import DEFAULT_PATH, compile_corrections, load_corrections
from elenchus.verify.diff import diff_order, diff_result
from elenchus.verify.verdict import verify_run

FULL = {o["id"]: o for o in generate_orders()}
MINIMAL = {o["id"]: o for o in generate_orders(minimal=True)}
ALL_CHECKS = compile_corrections(load_corrections(DEFAULT_PATH))
MAPPING_ONLY = [c for c in ALL_CHECKS if c.__class__.__name__ == "MappingCheck"]
# W-1001 and W-1007: Northwind, Austin (mapped). W-1005: Pinecrest, Boise (no mapping).
AUSTIN, AUSTIN_2, BOISE = "W-1001", "W-1007", "W-1005"
UNIQUE = "W-1002"  # Copperline Electric: one ERP customer


def _run(erp, tmp_path, order_id, fault_type=None):
    """The agent types the full order; ``fault_type`` makes it type a faulted copy."""
    base_url, db = erp
    injector = FaultInjector(1.0, types=(fault_type,)) if fault_type else None
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            rec = run_order(
                browser.new_page(), base_url, FULL[order_id],
                runs_path=tmp_path / "runs.jsonl", injector=injector,
            )
        finally:
            browser.close()
    assert (rec["fault"] is not None) == bool(fault_type)
    return rec, db


# --- generator ----------------------------------------------------------------

def test_minimal_orders_carry_only_name_and_storefront():
    assert list(MINIMAL) == list(FULL)
    for oid, m in MINIMAL.items():
        assert m["customer"] == {"name": FULL[oid]["customer"]["name"]}
        assert m["storefront"] == "web-" + FULL[oid]["customer"]["city"].lower().replace(" ", "-")
        assert {k: v for k, v in m.items() if k not in ("customer", "storefront")} == {
            k: v for k, v in FULL[oid].items() if k != "customer"
        }
    assert all("storefront" not in o for o in FULL.values())


def test_diff_order_refuses_minimal_sources(erp):
    with pytest.raises(ValueError, match="diff_result"):
        diff_order("unused.db", 1, MINIMAL[AUSTIN])


# --- full mode: both catch a wrong same-name customer ---------------------------

def test_full_mode_diff_and_replay_both_catch_wrong_customer(erp, tmp_path):
    rec, db = _run(erp, tmp_path, AUSTIN, WRONG_CUSTOMER)
    v = verify_run(db, rec, FULL, ALL_CHECKS)
    assert rec["claimed_success"] is True and v["passed"] is False
    assert v["caught_by"] == ["diff", "replay"]
    assert {"customer.city", "customer.email"} <= {m["field"] for m in v["diff_mismatches"]}
    assert [h["correction_id"] for h in v["replay_hits"]] == ["C-003"]
    assert v["unverifiable"] == []


def test_full_mode_diff_alone_catches_it_without_corrections(erp, tmp_path):
    rec, db = _run(erp, tmp_path, AUSTIN, WRONG_CUSTOMER)
    v = verify_run(db, rec, FULL)
    assert v["caught_by"] == ["diff"] and v["replay_hits"] == []


# --- source-minimal mode: diff alone cannot, replay can ---------------------------

def test_minimal_mode_wrong_customer_is_unverifiable_by_diff_alone(erp, tmp_path):
    rec, db = _run(erp, tmp_path, AUSTIN, WRONG_CUSTOMER)
    v = verify_run(db, rec, MINIMAL)  # no corrections
    assert v["diff_mismatches"] == [] and v["replay_hits"] == []
    assert [u["field"] for u in v["unverifiable"]] == ["customer"]
    assert v["passed"] is False and v["caught_by"] == []


def test_minimal_mode_replay_catches_wrong_customer(erp, tmp_path):
    rec, db = _run(erp, tmp_path, AUSTIN, WRONG_CUSTOMER)
    v = verify_run(db, rec, MINIMAL, ALL_CHECKS)
    assert v["passed"] is False and v["caught_by"] == ["replay"]
    assert v["diff_mismatches"] == [] and v["unverifiable"] == []
    assert {h["correction_id"] for h in v["replay_hits"]} == {"C-003", "C-006"}


def test_minimal_mode_mapping_alone_catches_a_wrong_customer(erp, tmp_path):
    rec, db = _run(erp, tmp_path, AUSTIN_2, WRONG_CUSTOMER)
    v = verify_run(db, rec, MINIMAL, MAPPING_ONLY)
    assert v["caught_by"] == ["replay"] and v["unverifiable"] == []
    (hit,) = v["replay_hits"]
    assert (hit["correction_id"], hit["field"], hit["right"]) == ("C-006", "customer.id", 1)
    assert hit["wrong"] == 2  # entered under the Denver account


# --- source-minimal mode: clean runs ------------------------------------------------

def test_minimal_clean_run_is_unverifiable_until_a_mapping_resolves_it(erp, tmp_path):
    rec, db = _run(erp, tmp_path, AUSTIN)
    bare = verify_run(db, rec, MINIMAL)
    assert bare["passed"] is False and [u["field"] for u in bare["unverifiable"]] == ["customer"]
    resolved = verify_run(db, rec, MINIMAL, MAPPING_ONLY)
    assert resolved["passed"] is True and resolved["caught_by"] == []
    assert resolved["unverifiable"] == [] and resolved["replay_hits"] == []


def test_minimal_same_name_customer_without_a_mapping_stays_unverifiable(erp, tmp_path):
    rec, db = _run(erp, tmp_path, BOISE)
    v = verify_run(db, rec, MINIMAL, ALL_CHECKS)
    assert v["passed"] is False and [u["field"] for u in v["unverifiable"]] == ["customer"]


def test_minimal_unique_name_customer_passes(erp, tmp_path):
    rec, db = _run(erp, tmp_path, UNIQUE)
    v = verify_run(db, rec, MINIMAL, ALL_CHECKS)
    assert v["passed"] is True and v["unverifiable"] == []
    assert diff_result(db, rec["erp_order_id"], MINIMAL[UNIQUE]).clean


def test_minimal_mode_still_catches_other_faults_by_diff(erp, tmp_path):
    rec, db = _run(erp, tmp_path, UNIQUE, STALE_PRICE)
    v = verify_run(db, rec, MINIMAL, ALL_CHECKS)
    assert v["passed"] is False and v["caught_by"] == ["diff"]


def test_verdict_shape(erp, tmp_path):
    rec, db = _run(erp, tmp_path, UNIQUE)
    v = verify_run(db, rec, FULL, ALL_CHECKS)
    assert set(v) == {
        "order_id", "erp_order_id", "claimed_success", "passed",
        "diff_mismatches", "replay_hits", "unverifiable", "caught_by",
    }
    assert v["passed"] is True and v["caught_by"] == []
