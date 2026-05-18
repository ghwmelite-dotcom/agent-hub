"""Tests for /status — orchestrator health snapshot."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.status_cmd import handle_status


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return (
        TaskRepository(temp_db_path),
        HandoffQueue(temp_db_path),
        GateRepository(temp_db_path),
        db,
    )


class _StubRunner:
    """Minimal AgentRunner stub exposing workspace + active_session_count."""

    def __init__(self, workspace: Path | None, sessions: int) -> None:
        self.workspace = workspace
        self.active_session_count = sessions


@pytest.mark.asyncio
async def test_status_empty_db(deps):
    _, _, _, db = deps
    reply = await handle_status(db_path=db.path)
    assert "Handoff queue (pending): 0" in reply
    assert "Unresolved design gates: 0" in reply
    assert "Tasks active: 0" in reply


@pytest.mark.asyncio
async def test_status_includes_runner_info_when_provided(deps, tmp_path):
    _, _, _, db = deps
    runner = _StubRunner(workspace=tmp_path, sessions=3)
    reply = await handle_status(db_path=db.path, runner=runner)
    assert f"Workspace: {tmp_path}" in reply
    assert "Active agent sessions: 3" in reply


@pytest.mark.asyncio
async def test_status_counts_pending_handoffs(deps):
    repo, queue, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="a"
    )
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="reviewer", message="b"
    )
    # Claimed rows do NOT count as pending
    await queue.claim()

    reply = await handle_status(db_path=db.path)
    assert "Handoff queue (pending): 1" in reply


@pytest.mark.asyncio
async def test_status_counts_unresolved_gates(deps):
    repo, _, gates, db = deps
    t1 = await repo.create(title="x", description="-", origin_chat_id=1)
    t2 = await repo.create(title="y", description="-", origin_chat_id=1)
    for t in (t1, t2):
        await repo.update(t.id, status=TaskStatus.PLANNING)
        await repo.update(t.id, status=TaskStatus.DESIGN_REVIEW)
        await gates.request(task_id=t.id, kind="design")
    # Resolve one — the other stays pending
    await gates.resolve(task_id=t1.id, kind="design", resolution="approved")

    reply = await handle_status(db_path=db.path)
    assert "Unresolved design gates: 1" in reply


@pytest.mark.asyncio
async def test_status_counts_tasks_by_status(deps):
    repo, _, _, db = deps
    # Make 1 in_progress, 1 review, 1 blocked
    for title, target in [
        ("a", TaskStatus.IN_PROGRESS),
        ("b", TaskStatus.REVIEW),
        ("c", TaskStatus.BLOCKED),
    ]:
        task = await repo.create(title=title, description="-", origin_chat_id=1)
        if target == TaskStatus.IN_PROGRESS:
            await repo.update(task.id, status=TaskStatus.PLANNING)
            await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
            await repo.update(task.id, status=TaskStatus.READY)
            await repo.update(task.id, status=TaskStatus.IN_PROGRESS)
        elif target == TaskStatus.REVIEW:
            await repo.update(task.id, status=TaskStatus.PLANNING)
            await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
            await repo.update(task.id, status=TaskStatus.READY)
            await repo.update(task.id, status=TaskStatus.IN_PROGRESS)
            await repo.update(task.id, status=TaskStatus.REVIEW)
        elif target == TaskStatus.BLOCKED:
            await repo.update(task.id, status=TaskStatus.BLOCKED)

    reply = await handle_status(db_path=db.path)
    assert "Tasks active: 2" in reply
    assert "blocked: 1" in reply
    assert "in_progress: 1" in reply
    assert "review: 1" in reply
    assert "blocked: 1" in reply
