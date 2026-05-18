"""Tests for /reject <id> <reason> — resolves the gate as rejected,
flips status back to planning, enqueues a handoff to architect with
the reason."""

import pytest

from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.reject_cmd import handle_reject


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return (
        TaskRepository(temp_db_path),
        GateRepository(temp_db_path),
        HandoffQueue(temp_db_path),
        db,
    )


@pytest.mark.asyncio
async def test_reject_resolves_gate_and_returns_planning(deps):
    repo, gates, queue, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    reply = await handle_reject(
        task_id=task.id,
        reason="d1 ping should be SELECT 1 not real query",
        db_path=db.path,
    )

    assert await gates.status(task_id=task.id, kind="design") == "rejected"
    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.PLANNING
    pending = await queue.pending()
    assert any(h.to_agent == "architect" and "SELECT 1" in h.message for h in pending)
    assert "rejected" in reply.lower() or "back to planning" in reply.lower()


@pytest.mark.asyncio
async def test_reject_unknown_task_returns_error(deps):
    repo, _, _, db = deps
    reply = await handle_reject(task_id=99999, reason="r", db_path=db.path)
    assert "not found" in reply.lower()


@pytest.mark.asyncio
async def test_reject_no_pending_gate_reports_no_op(deps):
    repo, _, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    reply = await handle_reject(task_id=task.id, reason="r", db_path=db.path)
    assert "no" in reply.lower() and "gate" in reply.lower()


@pytest.mark.asyncio
async def test_reject_empty_reason_returns_error(deps):
    repo, gates, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")
    reply = await handle_reject(task_id=task.id, reason="", db_path=db.path)
    assert "reason" in reply.lower()
