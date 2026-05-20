"""Tests for memory capture hooks — auto-write into project_memory at events."""

from __future__ import annotations

import pytest

from agent_hub.db import Database
from agent_hub.memory.capture import on_design_approved
from agent_hub.memory.store import MemoryStore
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return temp_db_path


@pytest.mark.asyncio
async def test_on_design_approved_writes_decision(db_path):
    # Create a real task so the FK on related_task is satisfied.
    repo = TaskRepository(db_path)
    task = await repo.create(
        title="Add user signup",
        description="desc",
        origin_chat_id=1,
    )
    await on_design_approved(
        db_path=db_path,
        workspace=r"C:\dev\foo",
        task_id=task.id,
        task_title="Add user signup",
        design_text="Use Auth0 + magic links. Reasoning: ...",
        agent_name="architect",
    )
    rows = await MemoryStore(db_path).list(
        workspace=r"C:\dev\foo", type="decision",
    )
    assert len(rows) == 1
    assert rows[0]["title"] == f"Task #{task.id}: Add user signup"
    assert "Auth0" in rows[0]["body"]
    assert rows[0]["agent_source"] == "architect"
    assert rows[0]["related_task"] == task.id


@pytest.mark.asyncio
async def test_on_design_approved_no_workspace_is_noop(db_path):
    """No workspace (e.g., user hasn't set one) → silently skip, don't crash."""
    await on_design_approved(
        db_path=db_path,
        workspace=None,
        task_id=42,
        task_title="t",
        design_text="d",
        agent_name="architect",
    )
    # No assertion on rows count by workspace=None — table is just empty.


@pytest.mark.asyncio
async def test_on_design_approved_attribution_matches_source(db_path):
    """Agent attribution must match the actual source of design_text."""
    # Create a real task so the FK on related_task is satisfied.
    repo = TaskRepository(db_path)
    task = await repo.create(
        title="EA: scalper variant",
        description="desc",
        origin_chat_id=1,
    )
    # When agent_name='quant' is passed, the saved row uses 'quant'.
    await on_design_approved(
        db_path=db_path,
        workspace=r"C:\dev\foo",
        task_id=task.id,
        task_title="EA: scalper variant",
        design_text="ATR-based stops with regime filter.",
        agent_name="quant",
    )
    rows = await MemoryStore(db_path).list(
        workspace=r"C:\dev\foo", type="decision",
    )
    assert len(rows) == 1
    assert rows[0]["agent_source"] == "quant"


@pytest.mark.asyncio
async def test_on_reject_writes_lesson(db_path):
    from agent_hub.memory.capture import on_reject
    # Create a real task first to satisfy FK on related_task.
    repo = TaskRepository(db_path)
    task = await repo.create(
        title="Build payments form",
        description="dummy",
        origin_chat_id=42,
    )
    await on_reject(
        db_path=db_path,
        workspace=r"C:\dev\foo",
        task_id=task.id,
        task_title="Build payments form",
        reason="Doesn't handle Stripe webhook retries",
    )
    rows = await MemoryStore(db_path).list(
        workspace=r"C:\dev\foo", type="lesson",
    )
    assert len(rows) == 1
    assert rows[0]["title"] == f"Rejected task #{task.id}: Build payments form"
    assert "Stripe webhook retries" in rows[0]["body"]
    assert rows[0]["agent_source"] == "user"
    assert rows[0]["related_task"] == task.id


@pytest.mark.asyncio
async def test_on_reject_no_workspace_is_noop(db_path):
    from agent_hub.memory.capture import on_reject
    await on_reject(
        db_path=db_path, workspace=None,
        task_id=1, task_title="t", reason="r",
    )


@pytest.mark.asyncio
async def test_reviewer_kickback_writes_lesson(db_path):
    """from=reviewer, to=fullstack-engineer is interpreted as a kickback."""
    from agent_hub.memory.capture import on_handoff_kickback
    task = await TaskRepository(db_path).create(
        title="t", description="d", origin_chat_id=42,
    )
    await on_handoff_kickback(
        db_path=db_path,
        workspace=r"C:\dev\foo",
        task_id=task.id,
        from_agent="reviewer",
        to_agent="fullstack-engineer",
        message="Unawaited promise in handlers.ts — please add `await`.",
    )
    rows = await MemoryStore(db_path).list(
        workspace=r"C:\dev\foo", type="lesson",
    )
    assert len(rows) == 1
    assert rows[0]["agent_source"] == "reviewer"
    assert "Unawaited promise" in rows[0]["body"]
    assert rows[0]["related_task"] == task.id


@pytest.mark.asyncio
async def test_qa_fail_writes_lesson(db_path):
    from agent_hub.memory.capture import on_handoff_kickback
    task = await TaskRepository(db_path).create(
        title="t", description="d", origin_chat_id=42,
    )
    await on_handoff_kickback(
        db_path=db_path,
        workspace=r"C:\dev\foo",
        task_id=task.id,
        from_agent="qa",
        to_agent="fullstack-engineer",
        message="Failing test: tests/test_x.py::test_y",
    )
    rows = await MemoryStore(db_path).list(
        workspace=r"C:\dev\foo", type="lesson",
    )
    assert rows[0]["agent_source"] == "qa"


@pytest.mark.asyncio
async def test_normal_forward_handoff_does_not_capture(db_path):
    """fullstack → reviewer is normal forward progress — no lesson."""
    from agent_hub.memory.capture import on_handoff_kickback
    task = await TaskRepository(db_path).create(
        title="t", description="d", origin_chat_id=42,
    )
    await on_handoff_kickback(
        db_path=db_path,
        workspace=r"C:\dev\foo",
        task_id=task.id,
        from_agent="fullstack-engineer",
        to_agent="reviewer",
        message="Done, ready for review",
    )
    rows = await MemoryStore(db_path).list(
        workspace=r"C:\dev\foo", type="lesson",
    )
    assert rows == []
