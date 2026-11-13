"""The fault injector is deterministic and changes the field it reports."""

from __future__ import annotations

import pytest

from elenchus.agent.faults import (
    FAULT_TYPES,
    OFF_BY_ONE_QUANTITY,
    STALE_PRICE,
    SWAPPED_SIZES,
    WRONG_CUSTOMER,
    FaultInjector,
)
from elenchus.orders import generate_orders

ORDERS = generate_orders()


def test_rate_zero_never_faults():
    inj = FaultInjector(0.0)
    assert all(inj.apply(o) == (o, None) for o in ORDERS)


def test_deterministic_per_order():
    a = [FaultInjector(0.5, seed=3).apply(o)[1] for o in ORDERS]
    b = [FaultInjector(0.5, seed=3).apply(o)[1] for o in reversed(ORDERS)][::-1]
    assert a == b
    assert any(a) and not all(a)


def test_input_order_not_mutated():
    before = repr(ORDERS)
    for o in ORDERS:
        FaultInjector(1.0).apply(o)
    assert repr(ORDERS) == before


def test_bad_arguments():
    with pytest.raises(ValueError):
        FaultInjector(1.5)
    with pytest.raises(ValueError):
        FaultInjector(1.0, types=("nope",))


@pytest.mark.parametrize("kind", FAULT_TYPES)
def test_each_type_changes_its_field(kind):
    inj = FaultInjector(1.0, types=(kind,))
    hits = 0
    for o in ORDERS:
        faulted, fault = inj.apply(o)
        if fault is None:
            assert kind == WRONG_CUSTOMER and o["customer"]["name"] not in {
                c["customer"]["name"] for c in ORDERS if "duplicate_name_customer" in c["tags"]
            }
            continue
        hits += 1
        assert fault["type"] == kind and faulted != o
        if kind == OFF_BY_ONE_QUANTITY:
            assert fault["injected"] == fault["original"] + 1
        elif kind == SWAPPED_SIZES:
            assert sum(fault["injected"].values()) == sum(fault["original"].values())
            assert fault["injected"] != fault["original"]
        elif kind == WRONG_CUSTOMER:
            assert fault["injected"]["name"] == fault["original"]["name"]
            assert fault["injected"]["email"] != fault["original"]["email"]
        elif kind == STALE_PRICE:
            assert float(fault["injected"]) < float(fault["original"])
    assert hits >= 3
