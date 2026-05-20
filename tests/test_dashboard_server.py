"""Tests for DashboardServer — HTTP routes + SSE."""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from agent_hub.dashboard.broker import DashboardBroker
from agent_hub.dashboard.events import TaskChanged
from agent_hub.dashboard.server import DashboardServer
from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
def unused_tcp_port():
    """Return a TCP port that's free at the moment we call."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def server_and_db(temp_db_path, unused_tcp_port):
    db = Database(temp_db_path)
    await db.init()
    await db.set_active_workspace(r"C:\dev\foo")

    broker = DashboardBroker(db_path=temp_db_path)
    server = DashboardServer(
        broker=broker, db_path=temp_db_path, port=unused_tcp_port,
    )
    await server.start()
    yield server, broker, temp_db_path, unused_tcp_port
    await server.stop()


@pytest.mark.asyncio
async def test_get_root_returns_html(server_and_db):
    _, _, _, port = server_and_db
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/") as resp:
            assert resp.status == 200
            text = await resp.text()
            assert "AGENT-HUB" in text or "<html" in text.lower()


@pytest.mark.asyncio
async def test_get_state_returns_json_shape(server_and_db):
    _, _, _, port = server_and_db
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/api/state") as resp:
            assert resp.status == 200
            payload = await resp.json()
            for key in ("workspace", "stats", "active_tasks",
                        "pending_gates", "recent_events"):
                assert key in payload


@pytest.mark.asyncio
async def test_get_task_returns_timeline(server_and_db):
    _, _, db_path, port = server_and_db
    repo = TaskRepository(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    await repo.comment(task.id, actor="architect", body="design here")

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"http://127.0.0.1:{port}/api/task/{task.id}"
        ) as resp:
            assert resp.status == 200
            payload = await resp.json()
            assert payload["task"]["id"] == task.id
            assert len(payload["events"]) >= 1


@pytest.mark.asyncio
async def test_get_task_404_for_unknown(server_and_db):
    _, _, _, port = server_and_db
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/api/task/9999") as resp:
            assert resp.status == 404


@pytest.mark.asyncio
async def test_sse_delivers_published_event(server_and_db):
    _, broker, _, port = server_and_db
    received: list = []

    async def consume():
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{port}/api/events",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                async for raw in resp.content:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: "):
                        received.append(json.loads(line[6:]))
                        if len(received) >= 1:
                            return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.2)  # let connection open

    await broker.publish(TaskChanged(
        workspace=r"C:\dev\foo", task={"id": 1, "title": "t"},
    ))

    await asyncio.wait_for(consumer, timeout=4.0)
    assert received[0]["kind"] == "task_changed"
    assert received[0]["task"]["id"] == 1


@pytest.mark.asyncio
async def test_sse_filters_other_workspaces(server_and_db):
    _, broker, _, port = server_and_db
    received: list = []
    done = asyncio.Event()

    async def consume():
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{port}/api/events",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                async for raw in resp.content:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: "):
                        received.append(json.loads(line[6:]))
                        done.set()
                        return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.2)

    # Publish an event from a DIFFERENT workspace — should be filtered.
    await broker.publish(TaskChanged(
        workspace=r"C:\other-workspace", task={"id": 1},
    ))
    await asyncio.sleep(0.05)
    # Then publish a matching one.
    await broker.publish(TaskChanged(
        workspace=r"C:\dev\foo", task={"id": 2},
    ))

    await asyncio.wait_for(done.wait(), timeout=4.0)
    consumer.cancel()
    try:
        await consumer
    except (asyncio.CancelledError, Exception):
        pass
    # Only the matching workspace's event arrived.
    assert all(
        evt.get("workspace") == r"C:\dev\foo"
        for evt in received
        if "workspace" in evt
    )
