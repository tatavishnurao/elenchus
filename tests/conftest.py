"""Shared fixtures."""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn

from elenchus.erp.app import create_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def erp(tmp_path):
    """A live ERP on a free port with a fresh DB. Yields (base_url, db_path)."""
    db = tmp_path / "erp.db"
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(db), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert thread.is_alive() and time.time() < deadline, "ERP did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", db
    server.should_exit = True
    thread.join(timeout=10)
