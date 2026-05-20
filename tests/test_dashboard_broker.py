"""Tests for DashboardBroker — pub/sub, snapshot, singleton helpers."""

from __future__ import annotations

import asyncio

import pytest

from agent_hub.dashboard.broker import (
    DashboardBroker,
    get_broker,
    set_broker,
)
from agent_hub.dashboard.events import (
    TaskChanged,
    TaskEvent,
    GateChanged,
    WorkspaceChanged,
)
from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return temp_db_path


@pytest.mark.asyncio
async def test_subscribe_then_publish_delivers_event(db_path):
    broker = DashboardBroker(db_path=db_path)
    received: list = []

    async def subscriber():
        async for event in broker.subscribe():
            received.append(event)
            if len(received) == 1:
                return

    task = asyncio.create_task(subscriber())
    await asyncio.sleep(0.01)  # let subscribe run

    evt = TaskChanged(workspace="ws", task={"id": 1})
    await broker.publish(evt)

    await asyncio.wait_for(task, timeout=1.0)
    assert received == [evt]


@pytest.mark.asyncio
async def test_multiple_subscribers_each_receive_event(db_path):
    broker = DashboardBroker(db_path=db_path)
    received_a: list = []
    received_b: list = []

    async def subscriber(bucket):
        async for event in broker.subscribe():
            bucket.append(event)
            if len(bucket) == 1:
                return

    t1 = asyncio.create_task(subscriber(received_a))
    t2 = asyncio.create_task(subscriber(received_b))
    await asyncio.sleep(0.01)

    evt = WorkspaceChanged(workspace="ws")
    await broker.publish(evt)

    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=1.0)
    assert received_a == [evt]
    assert received_b == [evt]


@pytest.mark.asyncio
async def test_full_queue_drops_subscriber(db_path):
    broker = DashboardBroker(db_path=db_path, queue_maxsize=2)
    # Subscribe but never consume — queue fills up.
    sub_iter = broker.subscribe().__aiter__()
    # Drive once to register (will block waiting for the first event).
    task = asyncio.create_task(sub_iter.__anext__())
    await asyncio.sleep(0.01)  # let registration happen

    # Push 3 events; queue holds 2, third should drop the subscriber.
    for i in range(3):
        await broker.publish(TaskChanged(workspace="ws", task={"id": i}))

    await asyncio.sleep(0.01)

    # Subscriber should now be removed from the active set.
    assert len(broker._subscribers) == 0  # noqa: SLF001  (testing internal)

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_snapshot_returns_expected_shape(db_path):
    db = Database(db_path)
    await db.set_active_workspace(r"C:\dev\foo")
    repo = TaskRepository(db_path)
    await repo.create(title="A", description="d", origin_chat_id=1)

    broker = DashboardBroker(db_path=db_path)
    snap = await broker.snapshot()
    assert "workspace" in snap
    assert "stats" in snap
    assert "active_tasks" in snap
    assert "pending_gates" in snap
    assert "recent_events" in snap
    assert snap["workspace"] == r"C:\dev\foo"


def test_get_broker_returns_none_when_unset():
    set_broker(None)
    assert get_broker() is None


def test_set_and_get_broker():
    b = DashboardBroker(db_path=None)
    set_broker(b)
    assert get_broker() is b
    set_broker(None)
