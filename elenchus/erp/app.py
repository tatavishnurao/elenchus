"""Mock ERP: server-rendered order entry with switchable UI variants.

The server reads each field by the name the active variant gives it. It checks
types and existence only (integer >= 0, known SKU, known colour, known
customer) and does not check that quantities are consistent with each other.
Wrong data is accepted as submitted, as in a real ERP.

Select a variant with ``?variant=<name>`` on any URL.
"""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .db import SIZE_COLUMNS, connect, init_db
from .seed import COLORS, SIZES
from .variants import VARIANTS, Variant, get_variant

TEMPLATES_DIR = Path(__file__).parent / "templates"
DEFAULT_DB = "out/erp.db"


def _parse_int(raw: object, what: str) -> int:
    text = str(raw if raw is not None else "").strip()
    try:
        value = int(text)
    except ValueError:
        raise HTTPException(400, f"{what} must be an integer") from None
    if value < 0:
        raise HTTPException(400, f"{what} must be >= 0")
    return value


def _parse_cents(raw: object, what: str) -> int:
    text = str(raw if raw is not None else "").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise HTTPException(400, f"{what} must be a decimal number") from None
    if not value.is_finite():
        raise HTTPException(400, f"{what} must be a decimal number")
    if value < 0:
        raise HTTPException(400, f"{what} must be >= 0")
    if value.as_tuple().exponent < -2:
        raise HTTPException(400, f"{what} has more than 2 decimal places")
    return int(value * 100)


def _money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    path = Path(db_path or os.environ.get("ELENCHUS_ERP_DB", DEFAULT_DB))
    init_db(path)

    app = FastAPI(title="Mock ERP")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["money"] = _money

    def variant_of(request: Request) -> Variant:
        try:
            return get_variant(request.query_params.get("variant"))
        except KeyError:
            raise HTTPException(404, "unknown variant") from None

    def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        variant = variant_of(request)
        ctx.update(
            variant=variant,
            variants=VARIANTS,
            vq=_vq(variant),
            sizes=SIZES,
            colors=COLORS,
        )
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def load_order(conn, order_id: int):
        order = conn.execute(
            "SELECT o.*, c.name AS customer_name, c.city AS customer_city "
            "FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.id = ?",
            (order_id,),
        ).fetchone()
        if order is None:
            raise HTTPException(404, "order not found")
        lines = conn.execute(
            "SELECT l.*, p.name AS product_name FROM order_lines l "
            "JOIN products p ON p.sku = l.sku WHERE l.order_id = ? ORDER BY l.line_no",
            (order_id,),
        ).fetchall()
        return order, lines

    def render_order(request: Request, order_id: int, status: int = 200):
        conn = connect(path)
        try:
            order, lines = load_order(conn, order_id)
            products = conn.execute("SELECT * FROM products ORDER BY sku").fetchall()
        finally:
            conn.close()
        return render(
            request, "order.html", status, order=order, lines=lines,
            products=products, order_id=order["id"],
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        variant_of(request)
        conn = connect(path)
        try:
            orders = conn.execute(
                "SELECT o.id, o.source_ref, o.status, c.name AS customer_name, "
                "c.city AS customer_city FROM orders o "
                "JOIN customers c ON c.id = o.customer_id ORDER BY o.id DESC"
            ).fetchall()
        finally:
            conn.close()
        return render(request, "index.html", orders=orders)

    @app.get("/orders/new", response_class=HTMLResponse)
    def new_order(request: Request):
        conn = connect(path)
        try:
            customers = conn.execute(
                "SELECT * FROM customers ORDER BY name, city"
            ).fetchall()
        finally:
            conn.close()
        return render(request, "new.html", customers=customers)

    @app.get("/orders/line-form-preview", response_class=HTMLResponse)
    def line_form_preview(request: Request):
        """The add-line form with no order behind it, so a drift check can read
        the form without creating a draft order in the ERP."""
        conn = connect(path)
        try:
            products = conn.execute("SELECT * FROM products ORDER BY sku").fetchall()
        finally:
            conn.close()
        return render(request, "line_form_preview.html", products=products, order_id="ID")

    @app.post("/orders")
    async def create_order(request: Request):
        variant = variant_of(request)
        form = await request.form()
        customer_id = _parse_int(form.get(variant.field_name("customer")), "customer")
        source_ref = str(form.get(variant.field_name("source_ref")) or "").strip()
        notes = str(form.get(variant.field_name("notes")) or "")
        conn = connect(path)
        try:
            if conn.execute(
                "SELECT 1 FROM customers WHERE id = ?", (customer_id,)
            ).fetchone() is None:
                raise HTTPException(400, "unknown customer")
            cur = conn.execute(
                "INSERT INTO orders (customer_id, source_ref, notes) VALUES (?, ?, ?)",
                (customer_id, source_ref, notes),
            )
            conn.commit()
            order_id = cur.lastrowid
        finally:
            conn.close()
        return RedirectResponse(f"/orders/{order_id}{_vq(variant)}", status_code=303)

    @app.get("/orders/{order_id}", response_class=HTMLResponse)
    def show_order(request: Request, order_id: int):
        return render_order(request, order_id)

    @app.post("/orders/{order_id}/lines")
    async def add_line(request: Request, order_id: int):
        variant = variant_of(request)
        form = await request.form()
        conn = connect(path)
        try:
            order, _ = load_order(conn, order_id)
            if order["status"] != "draft":
                conn.close()
                return render_order(request, order_id, status=409)

            sku = str(form.get(variant.field_name("sku")) or "").strip()
            color = str(form.get(variant.field_name("color")) or "").strip()
            if conn.execute("SELECT 1 FROM products WHERE sku = ?", (sku,)).fetchone() is None:
                raise HTTPException(400, "unknown SKU")
            if color not in COLORS:
                raise HTTPException(400, "unknown color")
            quantity = _parse_int(form.get(variant.field_name("quantity")), "quantity")
            price_cents = _parse_cents(form.get(variant.field_name("unit_price")), "unit price")

            qty = {size: 0 for size in SIZES}
            if variant.size_widget == "dropdown":
                size = str(form.get(variant.field_name("size")) or "").strip()
                if size not in SIZE_COLUMNS:
                    raise HTTPException(400, "unknown size")
                qty[size] = quantity
            else:
                for size in SIZES:
                    raw = form.get(variant.field_name(size))
                    qty[size] = _parse_int(raw, f"size {size}") if str(raw or "").strip() else 0

            line_no = conn.execute(
                "SELECT COALESCE(MAX(line_no), 0) + 1 FROM order_lines WHERE order_id = ?",
                (order_id,),
            ).fetchone()[0]
            cols = [
                "order_id", "line_no", "sku", "color",
                *(SIZE_COLUMNS[s] for s in SIZES),
                "quantity", "unit_price_cents",
            ]
            values = (order_id, line_no, sku, color, *(qty[s] for s in SIZES), quantity, price_cents)
            conn.execute(
                f"INSERT INTO order_lines ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})",
                values,
            )
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/orders/{order_id}{_vq(variant)}", status_code=303)

    @app.post("/orders/{order_id}/finish")
    def finish_order(request: Request, order_id: int):
        variant = variant_of(request)
        conn = connect(path)
        try:
            order, _ = load_order(conn, order_id)
            if order["status"] != "draft":
                conn.close()
                return render_order(request, order_id, status=409)
            conn.execute("UPDATE orders SET status = 'submitted' WHERE id = ?", (order_id,))
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/orders/{order_id}{_vq(variant)}", status_code=303)

    return app


def _vq(variant: Variant) -> str:
    return "" if variant.name == "baseline" else f"?variant={quote(variant.name)}"


app = create_app() if os.environ.get("ELENCHUS_ERP_AUTOSTART") else None
