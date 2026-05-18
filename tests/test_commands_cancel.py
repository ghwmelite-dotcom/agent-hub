"""Tests for /cancel <id> — abort a running task."""

from __future__ import annotations

import pytest

from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.cancel_cmd import handle_cancel


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), HandoffQueue(temp_db_path), db


class _RecordingRunner:
    """Minimal AgentRunner stub that records reset calls."""

    def __init__(self) -> None:
        self.reset_calls: list[tuple[str, int | None]] = []

    async def reset(self, agent_name: str, *, task_id: int | None = None) -> None:
        self.reset_calls.append((agent_name, task_id))


@pytest.mark.asyncio
async def test_cancel_in_progress_flips_to_blocked(deps):
    repo, queue, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await repo.update(task.id, status=TaskStatus.READY)
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS, owner="fullstack-engineer")

    reply = await handle_cancel(task_id=task.id, db_path=db.path)

    assert "cancelled" in reply.lower()
    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_cancel_drops_pending_handoffs(deps):
    repo, queue, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="go"
    )
    await queue.enqueue(
        task_id=task.id, from_agent="architect", to_agent="reviewer", message="check"
    )
    # Unrelated task's handoff should NOT be touched
    other = await repo.create(title="y", description="-", origin_chat_id=1)
    await queue.enqueue(
        task_id=other.id, from_agent="pm", to_agent="architect", message="other"
    )

    reply = await handle_cancel(task_id=task.id, db_path=db.path)

    assert "Dropped 2" in reply
    pending = await queue.pending()
    assert all(h.task_id != task.id for h in pending)
    assert any(h.task_id == other.id for h in pending)


@pytest.mark.asyncio
async def test_cancel_done_task_is_noop(deps):
    repo, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    for s in (
        TaskStatus.PLANNING, TaskStatus.DESIGN_REVIEW, TaskStatus.READY,
        TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE,
    ):
        await repo.update(task.id, status=s)

    reply = await handle_cancel(task_id=task.id, db_path=db.path)

    assert "already done" in reply.lower()
    assert (await repo.get(task.id)).status == TaskStatus.DONE


@pytest.mark.asyncio
async def test_cancel_already_blocked_is_noop(deps):
    repo, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.BLOCKED)

    reply = await handle_cancel(task_id=task.id, db_path=db.path)

    assert "already blocked" in reply.lower()


@pytest.mark.asyncio
async def test_cancel_unknown_task_returns_error(deps):
    _, _, db = deps
    reply = await handle_cancel(task_id=99999, db_path=db.path)
    assert "not found" in reply.lower()


@pytest.mark.asyncio
async def test_cancel_resets_owner_agent_session(deps):
    repo, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING, owner="architect")

    runner = _RecordingRunner()
    await handle_cancel(task_id=task.id, db_path=db.path, runner=runner)

    assert runner.reset_calls == [("architect", task.id)]


@pytest.mark.asyncio
async def test_cancel_writes_audit_comment(deps):
    repo, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)

    await handle_cancel(task_id=task.id, db_path=db.path)

    events = await repo.events(task.id)
    comments = [e for e in events if e.kind == "comment"]
    assert any("Cancelled by user" in (e.payload or {}).get("body", "") for e in comments)
