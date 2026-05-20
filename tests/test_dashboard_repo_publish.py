"""Verify each repo write triggers the right broker event."""

from __future__ import annotations

import pytest

from agent_hub.dashboard.broker import DashboardBroker, set_broker
from agent_hub.dashboard.events import (
    TaskChanged,
    TaskEvent,
    GateChanged,
    WorkspaceChanged,
)
from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    yield temp_db_path
    set_broker(None)


@pytest.fixture
def recording_broker(db_path):
    """A broker that records every published event for inspection."""
    class _Recording(DashboardBroker):
        def __init__(self):
            super().__init__(db_path=db_path)
            self.events: list = []

        async def publish(self, event):
            self.events.append(event)
            await super().publish(event)

    broker = _Recording()
    set_broker(broker)
    return broker


@pytest.mark.asyncio
async def test_task_create_publishes_task_changed(db_path, recording_broker):
    repo = TaskRepository(db_path)
    await repo.create(title="t", description="d", origin_chat_id=1)
    kinds = [type(e).__name__ for e in recording_broker.events]
    assert "TaskChanged" in kinds


@pytest.mark.asyncio
async def test_task_update_publishes_task_changed(db_path, recording_broker):
    repo = TaskRepository(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    recording_broker.events.clear()
    await repo.update(task.id, status=TaskStatus.PLANNING)
    assert any(isinstance(e, TaskChanged) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_task_comment_publishes_task_event(db_path, recording_broker):
    repo = TaskRepository(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    recording_broker.events.clear()
    await repo.comment(task.id, actor="architect", body="hi")
    assert any(isinstance(e, TaskEvent) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_gate_request_publishes_gate_changed(db_path, recording_broker):
    repo = TaskRepository(db_path)
    gates = GateRepository(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    recording_broker.events.clear()
    await gates.request(
        task_id=task.id, kind="design", artifact_path=None, summary="s",
    )
    assert any(isinstance(e, GateChanged) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_gate_resolve_publishes_gate_changed(db_path, recording_broker):
    repo = TaskRepository(db_path)
    gates = GateRepository(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    await gates.request(
        task_id=task.id, kind="design", artifact_path=None, summary="s",
    )
    recording_broker.events.clear()
    await gates.resolve(task_id=task.id, kind="design", resolution="approved")
    assert any(isinstance(e, GateChanged) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_handoff_enqueue_publishes_task_event(db_path, recording_broker):
    repo = TaskRepository(db_path)
    queue = HandoffQueue(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    recording_broker.events.clear()
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="hi",
    )
    assert any(isinstance(e, TaskEvent) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_set_active_workspace_publishes_workspace_changed(db_path, recording_broker):
    db = Database(db_path)
    recording_broker.events.clear()
    await db.set_active_workspace(r"C:\dev\foo")
    assert any(isinstance(e, WorkspaceChanged) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_publish_skipped_when_broker_unset(db_path):
    """Without a broker installed, repos still work (test-friendly)."""
    set_broker(None)
    repo = TaskRepository(db_path)
    # Just must not raise.
    await repo.create(title="t", description="d", origin_chat_id=1)
