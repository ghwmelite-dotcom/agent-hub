"""Tests for dashboard startup lifecycle (config + bind handling)."""

from __future__ import annotations

import socket

import pytest

from agent_hub.dashboard.broker import DashboardBroker
from agent_hub.dashboard.server import DashboardServer
from agent_hub.db import Database


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return temp_db_path


@pytest.fixture
def unused_tcp_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_start_binds_to_port(db_path, unused_tcp_port):
    server = DashboardServer(
        broker=DashboardBroker(db_path=db_path),
        db_path=db_path,
        port=unused_tcp_port,
    )
    await server.start()
    # Port should be in use now — second bind should fail.
    s = socket.socket()
    with pytest.raises(OSError):
        s.bind(("127.0.0.1", unused_tcp_port))
    s.close()
    await server.stop()


@pytest.mark.asyncio
async def test_stop_releases_port(db_path, unused_tcp_port):
    server = DashboardServer(
        broker=DashboardBroker(db_path=db_path),
        db_path=db_path,
        port=unused_tcp_port,
    )
    await server.start()
    await server.stop()
    # Port should be free now.
    s = socket.socket()
    s.bind(("127.0.0.1", unused_tcp_port))
    s.close()


@pytest.mark.asyncio
async def test_port_conflict_does_not_raise(db_path, unused_tcp_port):
    """If the port is already in use, start() logs and continues."""
    # Hold the port.
    holder = socket.socket()
    holder.bind(("127.0.0.1", unused_tcp_port))
    holder.listen(1)
    try:
        server = DashboardServer(
            broker=DashboardBroker(db_path=db_path),
            db_path=db_path,
            port=unused_tcp_port,
        )
        # Must not raise.
        await server.start()
        # Cleanup is safe even when bind failed.
        await server.stop()
    finally:
        holder.close()


def test_settings_includes_dashboard_port(monkeypatch):
    """Settings exposes dashboard_port with a sensible default."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "1")
    monkeypatch.delenv("DASHBOARD_PORT", raising=False)
    from agent_hub.config import load_settings
    s = load_settings()
    assert s.dashboard_port == 8765


def test_dashboard_port_can_be_overridden(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "1")
    monkeypatch.setenv("DASHBOARD_PORT", "9000")
    from agent_hub.config import load_settings
    s = load_settings()
    assert s.dashboard_port == 9000


def test_dashboard_port_zero_disables(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "1")
    monkeypatch.setenv("DASHBOARD_PORT", "0")
    from agent_hub.config import load_settings
    s = load_settings()
    assert s.dashboard_port == 0
