"""The seeded order generator is deterministic and includes the messy cases."""

from __future__ import annotations

from elenchus.erp.seed import COLORS, CUSTOMERS, PRODUCTS, SIZES
from elenchus.orders import DEFAULT_PATH, ORDER_COUNT, generate_orders, load_orders

SKUS = {p[0] for p in PRODUCTS}
CITY_BY_EMAIL = {c[1]: c[2] for c in CUSTOMERS}
NAMES = [c[0] for c in CUSTOMERS]


def test_deterministic_and_sixty_orders():
    a, b = generate_orders(), generate_orders()
    assert a == b
    assert len(a) == ORDER_COUNT == 60
    assert len({o["id"] for o in a}) == 60


def test_committed_file_matches_generator():
    assert load_orders(DEFAULT_PATH) == generate_orders()


def test_orders_refer_to_real_erp_data():
    for o in generate_orders():
        c = o["customer"]
        assert (c["name"], c["email"], c["city"]) in CUSTOMERS
        assert o["lines"]
        for line in o["lines"]:
            assert line["sku"] in SKUS
            assert line["color"] in COLORS
            assert set(line["sizes"]) == set(SIZES)
            assert line["quantity"] == sum(line["sizes"].values()) > 0


def test_messy_cases_present():
    orders = generate_orders()
    tagged = lambda tag: [o for o in orders if tag in o["tags"]]  # noqa: E731
    assert len(tagged("duplicate_sku")) >= 5
    assert len(tagged("zero_sizes")) >= 20
    assert len(tagged("odd_total")) >= 10
    dup = tagged("duplicate_name_customer")
    assert len(dup) >= 3
    for o in dup:
        assert NAMES.count(o["customer"]["name"]) == 2
    # Same name, different customer: the city or email is what tells them apart.
    assert len({(o["customer"]["name"], o["customer"]["city"]) for o in dup}) >= 2
