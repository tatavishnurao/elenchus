"""Replay checks catch a repeat of each recorded correction and nothing else."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from elenchus.agent.scripted import run_order
from elenchus.orders import generate_orders
from elenchus.verify.corrections import (
    DEFAULT_PATH,
    Correction,
    compile_corrections,
    load_corrections,
    replay_run,
)

CORRECTIONS = load_corrections(DEFAULT_PATH)
CHECKS = compile_corrections(CORRECTIONS)
BY_ID = {o["id"]: o for o in generate_orders()}
EXPECTED_IDS = ["C-001", "C-002", "C-003", "C-004", "C-005"]


class _Stub:
    """Injector that types a chosen faulted copy of the order."""

    def __init__(self, faulted: dict) -> None:
        self.faulted = faulted

    def apply(self, order):
        return self.faulted, {"type": "stub", "field": "n/a"}


def _repeat(correction_id: str, order: dict) -> dict:
    """The order as the agent typed it when the original mistake was made."""
    o = copy.deepcopy(order)
    line = o["lines"][0]
    if correction_id == "C-001":
        line["quantity"] += 1
    elif correction_id == "C-002":  # S and M swapped
        line["sizes"]["S"], line["sizes"]["M"] = line["sizes"]["M"], line["sizes"]["S"]
    elif correction_id == "C-003":  # same-name customer in the other city
        o["customer"] = {
            "name": "Northwind Outfitters",
            "email": "accounts@northwind-outfitters.example",
            "city": "Denver",
        }
    elif correction_id == "C-004":
        line["unit_price"] = "37.35"
    elif correction_id == "C-005":  # XXL read as XL
        line["sizes"]["XL"], line["sizes"]["2XL"] = line["sizes"]["2XL"], 0
    return o


def _run(erp, tmp_path, order, typed):
    base_url, db = erp
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            rec = run_order(
                page, base_url, order, runs_path=tmp_path / "runs.jsonl", injector=_Stub(typed)
            )
        finally:
            browser.close()
    return rec, db


# --- the data file ------------------------------------------------------------

def test_file_has_one_entry_per_fault_type_and_a_size_alias():
    assert [c.id for c in CORRECTIONS] == EXPECTED_IDS
    assert all(c.reason.strip() and c.wrong != c.right for c in CORRECTIONS)
    fields = [c.field.rsplit(".", 1)[-1] for c in CORRECTIONS]
    assert fields == ["quantity", "S", "email", "unit_price", "XL"]
    assert "XXL" in CORRECTIONS[-1].reason  # the alias entry says which alias


@pytest.mark.parametrize("c", CORRECTIONS, ids=lambda c: c.id)
def test_each_entry_points_at_a_real_order(c):
    assert c.order_pattern in BY_ID


# --- replay -------------------------------------------------------------------

@pytest.mark.parametrize("correction_id", EXPECTED_IDS)
def test_replay_catches_a_repeat_and_names_the_correction(erp, tmp_path, correction_id):
    correction = next(c for c in CORRECTIONS if c.id == correction_id)
    order = BY_ID[correction.order_pattern]
    rec, db = _run(erp, tmp_path, order, _repeat(correction_id, order))

    assert rec["claimed_success"] is True  # the agent still reports success
    hits = replay_run(db, rec, CHECKS)
    assert [h.correction_id for h in hits] == [correction_id]
    hit = hits[0]
    assert (hit.order_id, hit.erp_order_id) == (order["id"], rec["erp_order_id"])
    assert hit.field == correction.field and hit.right == correction.right
    assert hit.reason == correction.reason
    assert hit.to_dict()["correction_id"] == correction_id


@pytest.mark.parametrize("correction_id", EXPECTED_IDS)
def test_replay_is_quiet_on_a_correct_run(erp, tmp_path, correction_id):
    correction = next(c for c in CORRECTIONS if c.id == correction_id)
    order = BY_ID[correction.order_pattern]
    rec, db = _run(erp, tmp_path, order, order)
    assert replay_run(db, rec, CHECKS) == []


def test_replay_ignores_other_orders_and_missing_orders(erp, tmp_path):
    # W-1003 carries the C-001 wrong value nowhere, and C-001 is for W-1002 only.
    # Type W-1002's wrong quantity into a different order id: no hit.
    c = CORRECTIONS[0]
    order = BY_ID[c.order_pattern]
    rec, db = _run(erp, tmp_path, order, _repeat("C-001", order))
    assert replay_run(db, {**rec, "order_id": "W-9999"}, CHECKS) == []
    assert replay_run(db, {**rec, "erp_order_id": None}, CHECKS) == []
    assert replay_run(db, {**rec, "erp_order_id": 9999}, CHECKS) == []


# --- matching rules -----------------------------------------------------------

def test_glob_pattern_and_any_line_wildcard():
    c = Correction("T-1", "W-10*", "lines[*].quantity", 8, 7, "test")
    (check,) = compile_corrections([c])
    stored = {"lines[1].quantity": 3, "lines[2].quantity": 8, "lines[2].sku": "A1"}
    hits = check.run({"order_id": "W-1042", "erp_order_id": 1}, stored)
    assert [h.field for h in hits] == ["lines[2].quantity"]
    assert check.run({"order_id": "W-2042", "erp_order_id": 1}, stored) == []


def test_values_compare_by_type_not_by_spelling():
    price = compile_corrections([Correction("T-2", "*", "lines[1].unit_price", "37.3", 0, "t")])[0]
    assert price.run({"order_id": "x"}, {"lines[1].unit_price": "37.30"})
    text = compile_corrections([Correction("T-3", "*", "customer.name", "ACME  Co", "", "t")])[0]
    assert text.run({"order_id": "x"}, {"customer.name": "acme co"})


# --- loading ------------------------------------------------------------------

def _write(tmp_path: Path, *rows) -> Path:
    p = tmp_path / "c.jsonl"
    p.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n")
    return p


GOOD = {"id": "X", "order_pattern": "*", "field": "notes", "wrong": "a", "right": "b", "reason": "r"}


def test_load_rejects_bad_files(tmp_path):
    with pytest.raises(ValueError, match=":1: invalid JSON"):
        load_corrections(_write(tmp_path, "{nope"))
    with pytest.raises(ValueError, match=r"missing keys \['reason'\]"):
        load_corrections(_write(tmp_path, {k: v for k, v in GOOD.items() if k != "reason"}))
    with pytest.raises(ValueError, match="duplicate id"):
        load_corrections(_write(tmp_path, GOOD, GOOD))
    assert load_corrections(_write(tmp_path, GOOD, "")) == [Correction(**GOOD)]
