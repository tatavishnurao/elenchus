"""Deterministic Playwright agent written against the baseline ERP form.

It keys one webstore order into the ERP, finishes it, and appends a run record
to a JSONL file. It never checks what the ERP stored. The success claim only
says the agent got to the end without an error, so a verifier can compare the
claim with the database.

Usage:
    python -m elenchus.agent.scripted --base-url http://127.0.0.1:8000 --limit 3
"""

from __future__ import annotations

import argparse
import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

from ..orders import DEFAULT_PATH, load_orders
from .faults import DEFAULT_SEED, FaultInjector

DEFAULT_RUNS_PATH = Path("out/runs.jsonl")
MODE = "scripted"
STEP_TIMEOUT_MS = 5000
SIZES = ("S", "M", "L", "XL", "2XL")


class _Steps:
    """Collects per-step wall-clock timings in milliseconds."""

    def __init__(self) -> None:
        self.ms: dict[str, int] = {}

    @contextmanager
    def step(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.ms[name] = round((time.perf_counter() - start) * 1000)


def _enter_order(page: Page, base_url: str, order: dict, query: str, steps: _Steps) -> int:
    with steps.step("open_new_order"):
        page.goto(f"{base_url}/orders/new{query}")

    customer = order["customer"]
    with steps.step("fill_header"):
        page.get_by_label("Customer", exact=True).select_option(
            label=f"{customer['name']} ({customer['city']})"
        )
        page.get_by_label("Web order ref", exact=True).fill(order["source_ref"])
        page.get_by_label("Notes", exact=True).fill(order["notes"])

    with steps.step("create_order"):
        page.get_by_role("button", name="Create order").click()
        page.wait_for_url(re.compile(r"/orders/\d+"))
    erp_order_id = int(urlparse(page.url).path.rstrip("/").split("/")[-1])

    for i, line in enumerate(order["lines"], start=1):
        with steps.step(f"line_{i}"):
            page.get_by_label("Product", exact=True).select_option(value=line["sku"])
            page.get_by_label("Color", exact=True).select_option(label=line["color"])
            for size in SIZES:
                page.get_by_label(size, exact=True).fill(str(line["sizes"][size]))
            page.get_by_label("Quantity", exact=True).fill(str(line["quantity"]))
            page.get_by_label("Unit price", exact=True).fill(line["unit_price"])
            page.get_by_role("button", name="Save line").click()
            page.wait_for_load_state()

    with steps.step("finish"):
        page.get_by_role("button", name="Finish order").click()
        page.locator("#order-status").wait_for()
    return erp_order_id


def run_order(
    page: Page,
    base_url: str,
    order: dict,
    *,
    variant: str = "baseline",
    runs_path: str | Path = DEFAULT_RUNS_PATH,
    injector: FaultInjector | None = None,
) -> dict:
    """Enter one order, append its run record to ``runs_path`` and return it.

    With an injector, the agent may type a faulted copy of the order. The fault
    goes in the record's ``fault`` field, separate from ``claimed_success``.
    """
    entered, fault = injector.apply(order) if injector else (order, None)
    query = "" if variant == "baseline" else f"?variant={variant}"
    steps = _Steps()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    start = time.perf_counter()
    erp_order_id: int | None = None
    error: str | None = None
    try:
        erp_order_id = _enter_order(page, base_url.rstrip("/"), entered, query, steps)
    except Exception as exc:  # the agent reports failure; it does not raise
        error = f"{type(exc).__name__}: {exc}".splitlines()[0]
        # The order may exist even though a later step failed.
        match = re.search(r"/orders/(\d+)", page.url)
        if match:
            erp_order_id = int(match.group(1))

    record = {
        "order_id": order["id"],
        "variant": variant,
        "mode": MODE,
        "claimed_success": error is None,
        "fault": fault,
        "erp_order_id": erp_order_id,
        "steps_ms": steps.ms,
        "total_ms": round((time.perf_counter() - start) * 1000),
        "error": error,
        "started_at": started_at,
    }
    runs_path = Path(runs_path)
    runs_path.parent.mkdir(parents=True, exist_ok=True)
    with runs_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def run_orders(
    base_url: str,
    orders: list[dict],
    *,
    variant: str = "baseline",
    runs_path: str | Path = DEFAULT_RUNS_PATH,
    headless: bool = True,
    fault_rate: float = 0.0,
    fault_seed: int = DEFAULT_SEED,
) -> list[dict]:
    injector = FaultInjector(fault_rate, fault_seed) if fault_rate > 0 else None
    records = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            for order in orders:
                page = browser.new_page()
                page.set_default_timeout(STEP_TIMEOUT_MS)
                try:
                    records.append(
                        run_order(
                            page, base_url, order, variant=variant,
                            runs_path=runs_path, injector=injector,
                        )
                    )
                finally:
                    page.close()
        finally:
            browser.close()
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--orders", default=str(DEFAULT_PATH))
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--runs", default=str(DEFAULT_RUNS_PATH))
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--fault-rate", type=float, default=0.0)
    parser.add_argument("--fault-seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    orders = load_orders(args.orders)[: args.limit]
    for rec in run_orders(
        args.base_url,
        orders,
        variant=args.variant,
        runs_path=args.runs,
        headless=not args.headed,
        fault_rate=args.fault_rate,
        fault_seed=args.fault_seed,
    ):
        fault = rec["fault"]["type"] if rec["fault"] else "-"
        print(rec["order_id"], "claimed_success=" + str(rec["claimed_success"]), rec["erp_order_id"], fault)


if __name__ == "__main__":
    main()
