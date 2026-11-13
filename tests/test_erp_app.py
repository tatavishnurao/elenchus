"""Every ERP variant renders and can take one order end to end."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from elenchus.erp.app import create_app
from elenchus.erp.variants import VARIANTS, get_variant


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "erp.db"
    return TestClient(create_app(db)), db


def q(variant: str) -> str:
    return f"?variant={variant}"


def rows(db, sql, *args):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def make_order(client, v) -> int:
    r = client.post(
        f"/orders{q(v.name)}",
        data={v.field_name("customer"): "3", v.field_name("source_ref"): f"WEB-{v.name}", v.field_name("notes"): "rush"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    return int(r.headers["location"].split("/")[2].split("?")[0])


@pytest.mark.parametrize("name", list(VARIANTS))
def test_pages_render(env, name):
    client, _ = env
    v = get_variant(name)
    assert client.get(f"/{q(name)}").status_code == 200
    page = client.get(f"/orders/new{q(name)}")
    assert page.status_code == 200
    for key in v.header_order:
        assert f'name="{v.field_name(key)}"' in page.text
    order_id = make_order(client, v)
    page = client.get(f"/orders/{order_id}{q(name)}")
    assert page.status_code == 200
    assert v.finish_label in page.text
    save, reset = page.text.index(">Save line<"), page.text.index(">Reset<", page.text.index("Add line"))
    assert (reset < save) == v.buttons_swapped


@pytest.mark.parametrize("name", [n for n, v in VARIANTS.items() if v.size_widget == "grid"])
def test_grid_order_stored_as_submitted(env, name):
    client, db = env
    v = get_variant(name)
    order_id = make_order(client, v)
    # Sizes sum to 5 but quantity says 7: the server must not correct it.
    data = {
        v.field_name("sku"): "A1001", v.field_name("color"): "Navy",
        v.field_name("S"): "2", v.field_name("M"): "3",
        v.field_name("quantity"): "7", v.field_name("unit_price"): "12.50",
    }
    r = client.post(f"/orders/{order_id}/lines{q(name)}", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert client.post(f"/orders/{order_id}/finish{q(name)}", follow_redirects=False).status_code == 303
    (line,) = rows(db, "SELECT * FROM order_lines WHERE order_id = ?", order_id)
    assert (line["qty_s"], line["qty_m"], line["qty_l"], line["quantity"]) == (2, 3, 0, 7)
    assert line["unit_price_cents"] == 1250
    assert rows(db, "SELECT status FROM orders WHERE id = ?", order_id)[0]["status"] == "submitted"


def test_dropdown_two_lines(env):
    client, db = env
    v = get_variant("size_dropdown")
    order_id = make_order(client, v)
    for size, qty in (("M", "4"), ("XL", "2")):
        data = {
            v.field_name("sku"): "B1005", v.field_name("color"): "Black", v.field_name("size"): size,
            v.field_name("quantity"): qty, v.field_name("unit_price"): "30",
        }
        r = client.post(f"/orders/{order_id}/lines?variant=size_dropdown", data=data, follow_redirects=False)
        assert r.status_code == 303, r.text
    assert client.post(f"/orders/{order_id}/finish?variant=size_dropdown", follow_redirects=False).status_code == 303
    lines = rows(db, "SELECT * FROM order_lines WHERE order_id = ? ORDER BY line_no", order_id)
    got = [(l["line_no"], l["qty_s"], l["qty_m"], l["qty_l"], l["qty_xl"], l["qty_2xl"], l["quantity"]) for l in lines]
    assert got == [(1, 0, 4, 0, 0, 0, 4), (2, 0, 0, 0, 2, 0, 2)]
    assert all(l["unit_price_cents"] == 3000 for l in lines)


def test_renamed_reads_total_qty(env):
    client, db = env
    v = get_variant("renamed")
    order_id = make_order(client, v)
    data = {"sku": "A1002", "color": "Red", "qty_l": "1", "total_qty": "9", "price_each": "1", "quantity": "1"}
    assert client.post(f"/orders/{order_id}/lines?variant=renamed", data=data, follow_redirects=False).status_code == 303
    assert rows(db, "SELECT quantity FROM order_lines WHERE order_id = ?", order_id)[0]["quantity"] == 9


def test_submitted_order_rejects_lines(env):
    client, _ = env
    v = get_variant("baseline")
    order_id = make_order(client, v)
    client.post(f"/orders/{order_id}/finish", follow_redirects=False)
    r = client.post(
        f"/orders/{order_id}/lines",
        data={"sku": "A1001", "color": "Navy", "qty_s": "1", "quantity": "1", "unit_price": "1"},
    )
    assert r.status_code == 409
    assert "can no longer be changed" in r.text
    assert "Save line" not in r.text


@pytest.mark.parametrize(
    "override",
    [
        {"sku": "ZZ999"},
        {"color": "Purple"},
        {"qty_s": "-1"},
        {"qty_m": "1.5"},
        {"quantity": ""},
        {"unit_price": "1.234"},
        {"unit_price": "-1"},
        {"unit_price": "abc"},
        {"unit_price": "NaN"},
    ],
)
def test_line_validation(env, override):
    client, _ = env
    order_id = make_order(client, get_variant("baseline"))
    data = {"sku": "A1001", "color": "Navy", "qty_s": "1", "quantity": "1", "unit_price": "1.00"}
    data.update(override)
    assert client.post(f"/orders/{order_id}/lines", data=data).status_code == 400


def test_unknown_customer_and_variant(env):
    client, _ = env
    assert client.post("/orders", data={"customer_id": "999", "source_ref": "x"}).status_code == 400
    assert client.get("/orders/new?variant=nope").status_code == 404
