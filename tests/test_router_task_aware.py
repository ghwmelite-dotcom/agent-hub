"""Tests for parse_addressee gaining task-context awareness."""

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.db import Database
from agent_hub.orchestrator.router import classify_freeform_message
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return (
        AgentRegistry.load(),
        TaskRepository(temp_db_path),
        GateRepository(temp_db_path),
        db,
    )


@pytest.mark.asyncio
async def test_pending_gate_returns_gate_hint(deps):
    _, repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    result = await classify_freeform_message(
        chat_id=42, text="looks fine I guess", db_path=db.path,
    )
    assert result["kind"] == "pending_gate"
    assert result["task_id"] == task.id


@pytest.mark.asyncio
async def test_no_gate_returns_default(deps):
    _, repo, _, db = deps
    result = await classify_freeform_message(
        chat_id=42, text="hello pm", db_path=db.path,
    )
    assert result["kind"] == "default"


@pytest.mark.asyncio
async def test_multiple_open_gates_returns_first(deps):
    """If two tasks in the same chat have pending gates, the most recent
    is preferred."""
    _, repo, gates, db = deps
    t1 = await repo.create(title="t1", description="-", origin_chat_id=42)
    t2 = await repo.create(title="t2", description="-", origin_chat_id=42)
    for t in (t1, t2):
        await repo.update(t.id, status=TaskStatus.PLANNING)
        await repo.update(t.id, status=TaskStatus.DESIGN_REVIEW)
        await gates.request(task_id=t.id, kind="design")

    result = await classify_freeform_message(
        chat_id=42, text="?", db_path=db.path,
    )
    assert result["kind"] == "pending_gate"
    assert result["task_id"] == t2.id  # most recent
