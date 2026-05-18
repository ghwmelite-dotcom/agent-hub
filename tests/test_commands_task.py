import pytest

from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.task_cmd import handle_task


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), db


@pytest.mark.asyncio
async def test_task_detail_includes_title_status_recent_events(deps):
    repo, db = deps
    task = await repo.create(title="add /health", description="ping D1", origin_chat_id=1)
    await repo.comment(task.id, actor="pm", body="filed it")
    await repo.comment(task.id, actor="architect", body="design ready")

    reply = await handle_task(task_id=task.id, db_path=db.path)

    assert "add /health" in reply
    assert f"#{task.id}" in reply
    assert "pending" in reply.lower()
    assert "filed it" in reply
    assert "design ready" in reply
    assert "pm" in reply
    assert "architect" in reply


@pytest.mark.asyncio
async def test_task_unknown_returns_error(deps):
    _, db = deps
    reply = await handle_task(task_id=99999, db_path=db.path)
    assert "not found" in reply.lower()


@pytest.mark.asyncio
async def test_task_shows_zero_spend_for_new_task(deps):
    repo, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    reply = await handle_task(task_id=task.id, db_path=db.path)
    assert "Spent: $0" in reply


@pytest.mark.asyncio
async def test_task_shows_accumulated_spend(deps):
    repo, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.add_cost(task.id, 0.42)
    await repo.add_cost(task.id, 0.18)

    reply = await handle_task(task_id=task.id, db_path=db.path)
    assert "Spent: $0.6000" in reply


@pytest.mark.asyncio
async def test_task_truncates_to_recent_20_events(deps):
    repo, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    for i in range(25):
        await repo.comment(task.id, actor="pm", body=f"comment-{i}")

    reply = await handle_task(task_id=task.id, db_path=db.path)
    assert "comment-0" not in reply
    assert "comment-24" in reply
