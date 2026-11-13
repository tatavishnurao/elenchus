"""Run the scripted agent against a live ERP on a free port."""

from __future__ import annotations

import json
import sqlite3

from elenchus.agent.scripted import run_orders
from elenchus.orders import generate_orders


def test_agent_enters_three_orders(erp, tmp_path):
    base_url, db = erp
    runs = tmp_path / "out" / "runs.jsonl"
    orders = generate_orders()[:3]

    records = run_orders(base_url, orders, runs_path=runs)

    lines = [json.loads(x) for x in runs.read_text().splitlines()]
    assert lines == records and len(lines) == 3
    for rec, order in zip(lines, orders):
        assert rec["order_id"] == order["id"]
        assert rec["variant"] == "baseline" and rec["mode"] == "scripted"
        assert rec["claimed_success"] is True and rec["error"] is None
        assert isinstance(rec["erp_order_id"], int)
        assert {"open_new_order", "fill_header", "create_order", "finish"} <= set(rec["steps_ms"])
        assert all(isinstance(ms, int) and ms >= 0 for ms in rec["steps_ms"].values())

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        for rec, order in zip(lines, orders):
            row = conn.execute(
                "SELECT o.*, c.name, c.city FROM orders o JOIN customers c "
                "ON c.id = o.customer_id WHERE o.id = ?",
                (rec["erp_order_id"],),
            ).fetchone()
            assert row is not None
            assert row["status"] == "submitted"
            assert row["source_ref"] == order["source_ref"]
            assert (row["name"], row["city"]) == (
                order["customer"]["name"],
                order["customer"]["city"],
            )
            stored = conn.execute(
                "SELECT * FROM order_lines WHERE order_id = ? ORDER BY line_no",
                (row["id"],),
            ).fetchall()
            assert [(l["sku"], l["color"], l["quantity"]) for l in stored] == [
                (l["sku"], l["color"], l["quantity"]) for l in order["lines"]
            ]
    finally:
        conn.close()
