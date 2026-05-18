"""Tests for /approve <id> — resolves the pending design gate and
flips the task status to ready."""

import pytest

from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.approve_cmd import handle_approve


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), GateRepository(temp_db_path), db


@pytest.mark.asyncio
async def test_approve_resolves_pending_gate(deps):
    repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    reply = await handle_approve(task_id=task.id, db_path=db.path)

    assert await gates.status(task_id=task.id, kind="design") == "approved"
    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.READY
    assert "approved" in reply.lower() or f"#{task.id}" in reply


@pytest.mark.asyncio
async def test_approve_unknown_task_returns_error(deps):
    repo, _, db = deps
    reply = await handle_approve(task_id=99999, db_path=db.path)
    assert "not found" in reply.lower() or "unknown" in reply.lower()


@pytest.mark.asyncio
async def test_approve_with_no_pending_gate_reports_no_op(deps):
    repo, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    # Task is PENDING, no design gate requested yet.
    reply = await handle_approve(task_id=task.id, db_path=db.path)
    assert "no" in reply.lower() and "gate" in reply.lower()
