import pytest

from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.resume_cmd import handle_resume


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), HandoffQueue(temp_db_path), db


@pytest.mark.asyncio
async def test_resume_blocked_task_routes_to_pm(deps):
    repo, queue, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.BLOCKED)

    reply = await handle_resume(task_id=task.id, db_path=db.path)

    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.PLANNING

    pending = await queue.pending()
    assert any(h.to_agent == "pm" and f"#{task.id}" in h.message for h in pending)
    assert "resumed" in reply.lower() or "pm" in reply.lower()


@pytest.mark.asyncio
async def test_resume_in_progress_task_redispatches_to_owner(deps):
    """For tasks paused mid-flight (not blocked), resume routes back to
    the current owner with no status change."""
    repo, queue, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING, owner="fullstack-engineer")
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS)

    reply = await handle_resume(task_id=task.id, db_path=db.path)

    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.IN_PROGRESS
    pending = await queue.pending()
    assert any(h.to_agent == "fullstack-engineer" for h in pending)
    assert "resumed" in reply.lower()


@pytest.mark.asyncio
async def test_resume_unknown_task_returns_error(deps):
    _, _, db = deps
    reply = await handle_resume(task_id=99999, db_path=db.path)
    assert "not found" in reply.lower()
