import pytest

from agent_hub.db import Database
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), GateRepository(temp_db_path)


@pytest.mark.asyncio
async def test_request_creates_pending_gate(deps):
    repo, gates = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    gid = await gates.request(task_id=t.id, kind="design", summary="please review")
    assert gid > 0
    g = await gates.get(gid)
    assert g.task_id == t.id
    assert g.kind == "design"
    assert g.summary == "please review"
    assert g.resolution is None
    assert g.resolved_at is None


@pytest.mark.asyncio
async def test_status_pending_then_resolved(deps):
    repo, gates = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await gates.request(task_id=t.id, kind="design")
    assert await gates.status(task_id=t.id, kind="design") == "pending"

    await gates.resolve(task_id=t.id, kind="design", resolution="approved")
    assert await gates.status(task_id=t.id, kind="design") == "approved"


@pytest.mark.asyncio
async def test_status_none_when_no_gate(deps):
    repo, gates = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    assert await gates.status(task_id=t.id, kind="design") == "none"


@pytest.mark.asyncio
async def test_resolve_unknown_raises(deps):
    repo, gates = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    with pytest.raises(ValueError):
        await gates.resolve(task_id=t.id, kind="design", resolution="approved")


@pytest.mark.asyncio
async def test_resolve_already_resolved_is_noop(deps):
    repo, gates = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await gates.request(task_id=t.id, kind="design")
    await gates.resolve(task_id=t.id, kind="design", resolution="approved")
    # Second resolve should not raise and should not flip the resolution.
    await gates.resolve(task_id=t.id, kind="design", resolution="rejected")
    assert await gates.status(task_id=t.id, kind="design") == "approved"
