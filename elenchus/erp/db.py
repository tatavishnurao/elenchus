"""SQLite schema and helpers for the mock ERP."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .seed import COLORS, CUSTOMERS, PRODUCTS, SIZES

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL,
    email TEXT NOT NULL,
    city  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    sku              TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    list_price_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    source_ref  TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft',
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS order_lines (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id         INTEGER NOT NULL REFERENCES orders(id),
    line_no          INTEGER NOT NULL,
    sku              TEXT NOT NULL REFERENCES products(sku),
    color            TEXT NOT NULL,
    qty_s            INTEGER NOT NULL DEFAULT 0,
    qty_m            INTEGER NOT NULL DEFAULT 0,
    qty_l            INTEGER NOT NULL DEFAULT 0,
    qty_xl           INTEGER NOT NULL DEFAULT 0,
    qty_2xl          INTEGER NOT NULL DEFAULT 0,
    quantity         INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL
);
"""

# Column holding the quantity for each size.
SIZE_COLUMNS = {size: f"qty_{size.lower()}" for size in SIZES}

ORDER_STATUSES = ("draft", "submitted")


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str | Path) -> None:
    """Create the schema and seed customers and products if the DB is empty."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO customers (name, email, city) VALUES (?, ?, ?)", CUSTOMERS
            )
            conn.executemany(
                "INSERT INTO products (sku, name, list_price_cents) VALUES (?, ?, ?)",
                PRODUCTS,
            )
        conn.commit()
    finally:
        conn.close()


def reset_db(path: str | Path) -> None:
    """Delete the DB file and rebuild it with seed data."""
    p = Path(path)
    for suffix in ("", "-journal", "-wal", "-shm"):
        q = Path(str(p) + suffix)
        if q.exists():
            q.unlink()
    init_db(p)


__all__ = [
    "COLORS",
    "ORDER_STATUSES",
    "SIZE_COLUMNS",
    "connect",
    "init_db",
    "reset_db",
]
