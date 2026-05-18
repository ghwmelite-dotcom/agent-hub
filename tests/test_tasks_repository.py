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


@pytest.mark.asyncio
async def test_list_filters_by_status(repo):
    a = await repo.create(title="a", description="-", origin_chat_id=1)
    b = await repo.create(title="b", description="-", origin_chat_id=1)
    # Both are PENDING after creation.
    pending = await repo.list(status=TaskStatus.PENDING)
    assert {t.id for t in pending} == {a.id, b.id}

    none = await repo.list(status=TaskStatus.DONE)
    assert none == []


@pytest.mark.asyncio
async def test_list_filters_by_owner(repo):
    a = await repo.create(title="a", description="-", origin_chat_id=1, owner="pm")
    b = await repo.create(title="b", description="-", origin_chat_id=1, owner="architect")
    pm_tasks = await repo.list(owner="pm")
    assert [t.id for t in pm_tasks] == [a.id]


@pytest.mark.asyncio
async def test_tree_returns_root_with_descendants(repo):
    epic = await repo.create(title="epic", description="-", origin_chat_id=1)
    leaf1 = await repo.create(title="l1", description="-", origin_chat_id=1, parent_id=epic.id)
    leaf2 = await repo.create(title="l2", description="-", origin_chat_id=1, parent_id=epic.id)
    grand = await repo.create(title="gl", description="-", origin_chat_id=1, parent_id=leaf1.id)

    tree = await repo.tree(epic.id)
    assert tree["root"].id == epic.id
    descendant_ids = {t.id for t in tree["descendants"]}
    assert descendant_ids == {leaf1.id, leaf2.id, grand.id}


@pytest.mark.asyncio
async def test_tree_unknown_returns_none(repo):
    assert await repo.tree(99999) is None


@pytest.mark.asyncio
async def test_tree_with_cycle_terminates(repo, temp_db_path):
    """tree() must not infinite-loop if parent_id cycles exist."""
    import aiosqlite
    a = await repo.create(title="a", description="-", origin_chat_id=1)
    b = await repo.create(title="b", description="-", origin_chat_id=1, parent_id=a.id)
    # Force a cycle: make a's parent be b. (Not reachable via create's API,
    # but a future update method could land us here.)
    async with aiosqlite.connect(temp_db_path) as conn:
        await conn.execute("UPDATE tasks SET parent_id = ? WHERE id = ?", (b.id, a.id))
        await conn.commit()
    tree = await repo.tree(a.id)
    # Just must terminate. Exact descendant content depends on traversal —
    # the important assertion is that we got *something* back and no hang.
    assert tree is not None
    assert tree["root"].id == a.id
    # b is a's child via the original link, so it appears.
    descendant_ids = {t.id for t in tree["descendants"]}
    assert b.id in descendant_ids
    # No node appears twice.
    descendant_id_list = [t.id for t in tree["descendants"]]
    assert len(descendant_id_list) == len(set(descendant_id_list))
