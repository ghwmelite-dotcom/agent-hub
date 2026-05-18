"""Tests for the restart-resume scan — on boot, surface tasks that
were in flight (in_progress/review/planning) with no recent activity."""

from datetime import datetime, timedelta, timezone

import pytest

from agent_hub.db import Database
from agent_hub.orchestrator.resume import scan_stale_tasks
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.repository import TaskRepository
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), FakeMessageSurface(), db


async def _backdate_last_event(db_path, task_id, *, minutes: int):
    """Helper: rewrite the latest task_event ts to be N minutes ago."""
    import aiosqlite
    past = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE task_events SET ts = ? "
            "WHERE id = (SELECT id FROM task_events "
            "            WHERE task_id = ? ORDER BY ts DESC LIMIT 1)",
            (past, task_id),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_dms_user_about_in_flight_tasks(deps):
    repo, surface, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS)
    await _backdate_last_event(db.path, task.id, minutes=10)

    await scan_stale_tasks(db_path=db.path, surface=surface, stale_after_minutes=5)

    msgs = surface.dms_to(42)
    assert any(f"#{task.id}" in m for m in msgs)
    assert any("/resume" in m or "resume" in m.lower() for m in msgs)


@pytest.mark.asyncio
async def test_skips_recent_tasks(deps):
    repo, surface, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS)
    # Don't backdate — event is fresh.

    await scan_stale_tasks(db_path=db.path, surface=surface, stale_after_minutes=5)
    assert surface.sent == []


@pytest.mark.asyncio
async def test_skips_terminal_states(deps):
    repo, surface, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS)
    await repo.update(task.id, status=TaskStatus.REVIEW)
    await repo.update(task.id, status=TaskStatus.DONE)
    await _backdate_last_event(db.path, task.id, minutes=10)

    await scan_stale_tasks(db_path=db.path, surface=surface, stale_after_minutes=5)
    assert surface.sent == []
