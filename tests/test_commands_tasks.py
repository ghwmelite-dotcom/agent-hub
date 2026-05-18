import pytest

from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.tasks_cmd import handle_tasks


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), db


@pytest.mark.asyncio
async def test_tasks_lists_non_done_tasks(deps):
    repo, db = deps
    a = await repo.create(title="alpha", description="-", origin_chat_id=1)
    b = await repo.create(title="beta", description="-", origin_chat_id=1)
    c = await repo.create(title="gamma done", description="-", origin_chat_id=1)
    await repo.update(c.id, status=TaskStatus.PLANNING)
    await repo.update(c.id, status=TaskStatus.IN_PROGRESS)
    await repo.update(c.id, status=TaskStatus.REVIEW)
    await repo.update(c.id, status=TaskStatus.DONE)

    reply = await handle_tasks(db_path=db.path)

    assert f"#{a.id}" in reply
    assert f"#{b.id}" in reply
    assert f"#{c.id}" not in reply
    assert "alpha" in reply
    assert "beta" in reply


@pytest.mark.asyncio
async def test_tasks_empty_returns_friendly_message(deps):
    _, db = deps
    reply = await handle_tasks(db_path=db.path)
    assert "no" in reply.lower() or "empty" in reply.lower()


@pytest.mark.asyncio
async def test_tasks_groups_by_status(deps):
    repo, db = deps
    a = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(a.id, status=TaskStatus.PLANNING)

    b = await repo.create(title="y", description="-", origin_chat_id=1)
    await repo.update(b.id, status=TaskStatus.PLANNING)
    await repo.update(b.id, status=TaskStatus.DESIGN_REVIEW)

    reply = await handle_tasks(db_path=db.path)
    assert "planning" in reply.lower()
    assert "design_review" in reply.lower() or "design review" in reply.lower()
