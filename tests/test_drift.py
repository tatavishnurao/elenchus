"""Drift detection flags each UI variant and keeps a stable baseline fingerprint."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from elenchus.agent.scripted import run_orders
from elenchus.erp.variants import VARIANTS
from elenchus.orders import generate_orders
from elenchus.verify.drift import (
    DriftBlocked,
    check_drift,
    fetch_fingerprint,
    fingerprint_pages,
    load_baseline,
    require_no_drift,
    save_baseline,
)

REPO_BASELINE = Path(__file__).parents[1] / "data" / "fingerprints" / "baseline.json"
DRIFTED = [v for v in VARIANTS if v != "baseline"]
EXPECTED = {
    "renamed": {"label", "input_name", "button_text"},
    "reordered": {"element_order"},
    "moved_save": {"button_order"},
    "size_dropdown": {"removed", "added", "size_widget"},
}


def test_there_are_four_drift_variants():
    assert sorted(DRIFTED) == sorted(EXPECTED)


def test_baseline_fingerprint_is_stable_across_runs(erp):
    base_url, _ = erp
    first = fetch_fingerprint(base_url)
    second = fetch_fingerprint(base_url)
    assert first == second and len(first["hash"]) == 16


def test_baseline_passes_twice_in_a_row(erp, tmp_path):
    base_url, _ = erp
    path = save_baseline(fetch_fingerprint(base_url), tmp_path / "b.json")
    for _ in range(2):
        report = check_drift(base_url, "baseline", path)
        assert report.ok and report.changes == []
        assert require_no_drift(base_url, "baseline", path) == report


def test_committed_baseline_matches_the_baseline_form(erp):
    base_url, _ = erp
    assert load_baseline(REPO_BASELINE)["hash"] == fetch_fingerprint(base_url)["hash"]
    assert check_drift(base_url, "baseline", REPO_BASELINE).ok


def test_fingerprint_ignores_erp_data(erp):
    base_url, db = erp
    before = fetch_fingerprint(base_url)["hash"]
    run_orders(base_url, generate_orders()[:1], runs_path=db.parent / "runs.jsonl")
    assert fetch_fingerprint(base_url)["hash"] == before


@pytest.mark.parametrize("variant", DRIFTED)
def test_each_variant_is_flagged_with_the_changed_elements(erp, variant):
    base_url, _ = erp
    report = check_drift(base_url, variant, REPO_BASELINE)
    assert not report.ok and report.current_hash != report.baseline_hash
    assert {c["kind"] for c in report.changes} == EXPECTED[variant]
    assert all(c["where"] and c["detail"] for c in report.changes)
    text = report.format()
    assert report.baseline_hash in text and report.current_hash in text


def test_flagged_changes_name_the_elements(erp):
    base_url, _ = erp
    details = lambda v: "\n".join(c["detail"] for c in check_drift(base_url, v, REPO_BASELINE).changes)
    assert "'Customer' -> 'Account'" in details("renamed")
    assert "'customer_id' -> 'account_id'" in details("renamed")
    assert "'Finish order' -> 'Submit order'" in details("renamed")
    assert "Notes, Web order ref, Customer" in details("reordered")
    assert "['Reset', 'Save line']" in details("moved_save")
    assert "size widget grid -> dropdown" in details("size_dropdown")


@pytest.mark.parametrize("variant", DRIFTED)
def test_drifted_run_is_blocked_before_anything_is_entered(erp, tmp_path, variant):
    base_url, db = erp
    runs = tmp_path / "runs.jsonl"
    with pytest.raises(DriftBlocked) as blocked:
        run_orders(
            base_url, generate_orders()[:1], variant=variant,
            runs_path=runs, drift_baseline=REPO_BASELINE,
        )
    assert not blocked.value.report.ok and "UI drift" in str(blocked.value)
    assert not runs.exists()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_clean_run_goes_ahead_with_drift_check_on(erp, tmp_path):
    base_url, _ = erp
    (rec,) = run_orders(
        base_url, generate_orders()[:1], runs_path=tmp_path / "r.jsonl",
        drift_baseline=REPO_BASELINE,
    )
    assert rec["claimed_success"] is True


def test_baseline_file_round_trips_and_detects_hand_edits(tmp_path):
    fp = fingerprint_pages({"p": "<form><label for=a>A</label><input id=a name=a></form>"})
    path = save_baseline(fp, tmp_path / "sub" / "b.json")
    assert load_baseline(path) == fp
    data = json.loads(path.read_text())
    data["structure"]["pages"]["p"]["forms"][0]["fields"][0]["name"] = "b"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="hash does not match"):
        load_baseline(path)


def test_parser_reads_labels_buttons_and_ignores_options_and_actions():
    def html(action, extra_option):
        return (
            f'<form action="{action}"><label>Qty <input type=number name=q></label>'
            f'<select name=s><option value="">--</option>{extra_option}</select>'
            '<input type=hidden name=csrf value=abc><button>Go</button>'
            '<input type=reset value=Clear></form>'
        )

    a = fingerprint_pages({"p": html("/orders/1/lines", "")})
    b = fingerprint_pages({"p": html("/orders/2/lines", "<option value=X>X</option>")})
    assert a == b
    (form,) = a["structure"]["pages"]["p"]["forms"]
    assert [f["label"] for f in form["fields"]] == ["Qty", ""]
    assert form["buttons"] == [
        {"type": "submit", "text": "Go"}, {"type": "reset", "text": "Clear"},
    ]
