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


from agent_hub.state_machine import InvalidTransition


@pytest.mark.asyncio
async def test_update_status_valid_transition(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    updated = await repo.update(t.id, status=TaskStatus.PLANNING)
    assert updated.status == TaskStatus.PLANNING


@pytest.mark.asyncio
async def test_update_status_invalid_transition_raises(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    with pytest.raises(InvalidTransition):
        await repo.update(t.id, status=TaskStatus.DONE)


@pytest.mark.asyncio
async def test_update_owner(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    updated = await repo.update(t.id, owner="pm")
    assert updated.owner == "pm"


@pytest.mark.asyncio
async def test_update_unknown_task_returns_none(repo):
    assert await repo.update(99999, owner="x") is None


@pytest.mark.asyncio
async def test_update_refreshes_updated_at(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    original = t.updated_at
    import asyncio
    await asyncio.sleep(0.01)
    updated = await repo.update(t.id, owner="pm")
    assert updated.updated_at > original


@pytest.mark.asyncio
async def test_comment_appends_event(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    event_id = await repo.comment(t.id, actor="pm", body="filed the task")
    assert event_id > 0

    events = await repo.events(t.id)
    assert len(events) == 1
    assert events[0].kind == "comment"
    assert events[0].actor == "pm"
    assert events[0].payload == {"body": "filed the task"}


@pytest.mark.asyncio
async def test_events_ordered_by_time(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    import asyncio
    await repo.comment(t.id, actor="pm", body="one")
    await asyncio.sleep(0.01)
    await repo.comment(t.id, actor="architect", body="two")
    events = await repo.events(t.id)
    assert [e.payload["body"] for e in events] == ["one", "two"]


@pytest.mark.asyncio
async def test_events_limit_returns_recent(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    import asyncio
    for i in range(5):
        await repo.comment(t.id, actor="pm", body=str(i))
        await asyncio.sleep(0.001)  # ensure ordering by ts
    events = await repo.events(t.id, limit=2)
    assert [e.payload["body"] for e in events] == ["3", "4"]


@pytest.mark.asyncio
async def test_status_change_writes_event(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(t.id, status=TaskStatus.PLANNING)
    events = await repo.events(t.id)
    kinds = [e.kind for e in events]
    assert "status_change" in kinds
