"""Fault injector for the scripted agent.

``FaultInjector.apply`` returns the order the agent should type into the ERP and
a description of the fault, if one was injected. The decision depends only on
(seed, order id), so it does not change with run order. The fault description
goes in the run record on its own, apart from the agent's success claim.
"""

from __future__ import annotations

import copy
import random
from decimal import Decimal

from ..erp.seed import CUSTOMERS, SIZES

DEFAULT_SEED = 7

OFF_BY_ONE_QUANTITY = "off_by_one_quantity"
SWAPPED_SIZES = "swapped_sizes"
WRONG_CUSTOMER = "wrong_customer"
STALE_PRICE = "stale_price"
FAULT_TYPES = (OFF_BY_ONE_QUANTITY, SWAPPED_SIZES, WRONG_CUSTOMER, STALE_PRICE)


def _fault(kind: str, field: str, original, injected) -> dict:
    return {"type": kind, "field": field, "original": original, "injected": injected}


def _other_customer(customer: dict) -> dict | None:
    """The other ERP customer with the same name, or None if the name is unique."""
    others = [
        c for c in CUSTOMERS if c[0] == customer["name"] and c[1] != customer["email"]
    ]
    if not others:
        return None
    name, email, city = others[0]
    return {"name": name, "email": email, "city": city}


def _stale(price: str) -> str:
    cents = int(Decimal(price) * 100)
    old = cents * 9 // 10
    if old == cents:
        old = cents - 1
    return f"{old // 100}.{old % 100:02d}"


class FaultInjector:
    def __init__(
        self,
        rate: float = 0.0,
        seed: int = DEFAULT_SEED,
        types: tuple[str, ...] = FAULT_TYPES,
    ) -> None:
        if not 0.0 <= rate <= 1.0:
            raise ValueError("rate must be between 0 and 1")
        unknown = set(types) - set(FAULT_TYPES)
        if unknown:
            raise ValueError(f"unknown fault types: {sorted(unknown)}")
        self.rate = rate
        self.seed = seed
        self.types = tuple(types)

    def apply(self, order: dict) -> tuple[dict, dict | None]:
        rng = random.Random(f"{self.seed}:{order['id']}")
        if self.rate <= 0 or rng.random() >= self.rate:
            return order, None
        applicable = [t for t in self.types if self._applicable(t, order)]
        if not applicable:
            return order, None
        kind = rng.choice(applicable)
        faulted = copy.deepcopy(order)
        return faulted, getattr(self, f"_inject_{kind}")(faulted, rng)

    @staticmethod
    def _applicable(kind: str, order: dict) -> bool:
        if kind == WRONG_CUSTOMER:
            return _other_customer(order["customer"]) is not None
        return bool(order["lines"])

    @staticmethod
    def _inject_off_by_one_quantity(order: dict, rng: random.Random) -> dict:
        i = rng.randrange(len(order["lines"]))
        line = order["lines"][i]
        original = line["quantity"]
        line["quantity"] = original + 1
        return _fault(OFF_BY_ONE_QUANTITY, f"lines[{i + 1}].quantity", original, original + 1)

    @staticmethod
    def _inject_swapped_sizes(order: dict, rng: random.Random) -> dict:
        i = rng.randrange(len(order["lines"]))
        sizes = order["lines"][i]["sizes"]
        pairs = [(a, b) for a in SIZES for b in SIZES if a < b and sizes[a] != sizes[b]]
        a, b = rng.choice(pairs)
        original = dict(sizes)
        sizes[a], sizes[b] = sizes[b], sizes[a]
        return _fault(SWAPPED_SIZES, f"lines[{i + 1}].sizes", original, dict(sizes))

    @staticmethod
    def _inject_wrong_customer(order: dict, rng: random.Random) -> dict:
        original = dict(order["customer"])
        order["customer"] = _other_customer(original)
        return _fault(WRONG_CUSTOMER, "customer", original, dict(order["customer"]))

    @staticmethod
    def _inject_stale_price(order: dict, rng: random.Random) -> dict:
        i = rng.randrange(len(order["lines"]))
        line = order["lines"][i]
        original = line["unit_price"]
        line["unit_price"] = _stale(original)
        return _fault(STALE_PRICE, f"lines[{i + 1}].unit_price", original, line["unit_price"])
