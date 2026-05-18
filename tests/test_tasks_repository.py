import pytest
from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def repo(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path)


@pytest.mark.asyncio
async def test_create_returns_task_with_id(repo):
    t = await repo.create(
        title="add /health endpoint",
        description="ping D1 and return OK",
        origin_chat_id=12345,
    )
    assert t.id > 0
    assert t.title == "add /health endpoint"
    assert t.status == TaskStatus.PENDING
    assert t.parent_id is None
    assert t.owner is None


@pytest.mark.asyncio
async def test_get_returns_created_task(repo):
    created = await repo.create(title="x", description="y", origin_chat_id=1)
    fetched = await repo.get(created.id)
    assert fetched.id == created.id
    assert fetched.title == "x"


@pytest.mark.asyncio
async def test_get_unknown_returns_none(repo):
    assert await repo.get(99999) is None


@pytest.mark.asyncio
async def test_create_with_parent(repo):
    parent = await repo.create(title="epic", description="...", origin_chat_id=1)
    child = await repo.create(
        title="leaf", description="...", origin_chat_id=1, parent_id=parent.id,
    )
    assert child.parent_id == parent.id
