"""Seeded generator for the webstore orders the agent has to key into the ERP.

Run ``python -m elenchus.orders`` to rewrite ``data/orders/orders.json``.
The output depends only on ``SEED``, so the file is reproducible.

Source orders are internally consistent (each line's quantity equals the sum
of its sizes). They are messy in other ways: repeated SKUs, sparse size runs,
odd totals, and customers whose name matches two ERP customers.

Source-minimal mode (``minimal=True``) models a webstore export that identifies
the customer by name alone: ``customer`` is ``{"name": ...}`` and the order
carries a ``storefront`` id instead of the email and city. Everything else is
the same as the full order with the same id.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from .erp.seed import COLORS, CUSTOMERS, PRODUCTS, SIZES

SEED = 20260101
ORDER_COUNT = 60
DEFAULT_PATH = Path("data/orders/orders.json")
MINIMAL_PATH = Path("data/orders/orders.minimal.json")

# Positions (0-based) forced to use a customer whose name is shared by two ERP
# customers in different cities. The order carries the city and email.
DUPLICATE_NAME_POSITIONS = (4, 19, 33, 47, 58)
# Positions forced to repeat a SKU within the order.
DUPLICATE_SKU_POSITIONS = tuple(range(2, ORDER_COUNT, 6))

_NOTES = ("", "", "", "Gift wrap", "Ship together", "Rush", "Call before delivery")


def _duplicate_name_customers() -> list[tuple[str, str, str]]:
    names = [c[0] for c in CUSTOMERS]
    return [c for c in CUSTOMERS if names.count(c[0]) > 1]


def storefront_for(city: str) -> str:
    """The webstore a customer's orders come from, for example ``web-austin``."""
    return "web-" + city.lower().replace(" ", "-")


def minimize(order: dict) -> dict:
    """The source-minimal view of a full order: customer name plus storefront."""
    out = dict(order)
    out["customer"] = {"name": order["customer"]["name"]}
    out["storefront"] = storefront_for(order["customer"]["city"])
    return out


def _price(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _size_run(rng: random.Random, odd: bool) -> dict[str, int]:
    """A sparse size run: one to three sizes with stock, the rest zero."""
    picked = rng.sample(SIZES, rng.randint(1, 3))
    run = {size: 0 for size in SIZES}
    for size in picked:
        run[size] = rng.randint(1, 12)
    if odd and sum(run.values()) % 2 == 0:
        run[picked[0]] += 1
    return run


def _line(rng: random.Random, sku: str, price_cents: int, color: str, odd: bool) -> dict:
    sizes = _size_run(rng, odd)
    return {
        "sku": sku,
        "color": color,
        "sizes": sizes,
        "quantity": sum(sizes.values()),
        "unit_price": _price(price_cents),
    }


def _tags(customer: tuple[str, str, str], lines: list[dict]) -> list[str]:
    tags = []
    skus = [line["sku"] for line in lines]
    if len(skus) != len(set(skus)):
        tags.append("duplicate_sku")
    if any(0 in line["sizes"].values() for line in lines):
        tags.append("zero_sizes")
    if any(line["quantity"] % 2 for line in lines):
        tags.append("odd_total")
    if customer in _duplicate_name_customers():
        tags.append("duplicate_name_customer")
    if len(lines) > 1:
        tags.append("multi_line")
    return tags


def generate_orders(
    seed: int = SEED, count: int = ORDER_COUNT, minimal: bool = False
) -> list[dict]:
    rng = random.Random(seed)
    dup_name = _duplicate_name_customers()
    orders = []
    for pos in range(count):
        if pos in DUPLICATE_NAME_POSITIONS:
            customer = rng.choice(dup_name)
        else:
            customer = rng.choice(CUSTOMERS)

        n_lines = rng.choice((1, 1, 2, 2, 3))
        lines = []
        for _ in range(n_lines):
            sku, _name, price = rng.choice(PRODUCTS)
            lines.append(_line(rng, sku, price, rng.choice(COLORS), odd=rng.random() < 0.4))

        if pos in DUPLICATE_SKU_POSITIONS:
            # Same SKU again, in the same or another colour.
            if len(lines) == 1:
                lines.append(dict(lines[0]))
            else:
                lines[-1] = dict(lines[0])
            repeat = lines[-1]
            repeat["color"] = rng.choice(COLORS)
            repeat["sizes"] = _size_run(rng, odd=True)
            repeat["quantity"] = sum(repeat["sizes"].values())

        name, email, city = customer
        orders.append(
            {
                "id": f"W-{1001 + pos}",
                "source_ref": f"WEB-{rng.randint(100000, 999999)}",
                "customer": {"name": name, "email": email, "city": city},
                "notes": rng.choice(_NOTES),
                "lines": lines,
                "tags": _tags(customer, lines),
            }
        )
    return [minimize(o) for o in orders] if minimal else orders


def write_orders(path: str | Path = DEFAULT_PATH, minimal: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(generate_orders(minimal=minimal), indent=2) + "\n", encoding="utf-8")
    return path


def load_orders(path: str | Path = DEFAULT_PATH) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Write the seeded webstore orders.")
    ap.add_argument("--minimal", action="store_true", help="source-minimal mode")
    ap.add_argument("--out", help=f"default {DEFAULT_PATH}, or {MINIMAL_PATH} with --minimal")
    a = ap.parse_args()
    target = a.out or (MINIMAL_PATH if a.minimal else DEFAULT_PATH)
    print(f"wrote {write_orders(target, a.minimal)}")
