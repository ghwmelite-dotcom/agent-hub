import pytest

from agent_hub.db import Database
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), HandoffQueue(temp_db_path)


@pytest.mark.asyncio
async def test_enqueue_returns_id(deps):
    repo, queue = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    qid = await queue.enqueue(
        task_id=t.id, from_agent="pm", to_agent="architect", message="design this",
    )
    assert qid > 0


@pytest.mark.asyncio
async def test_pending_returns_unclaimed(deps):
    repo, queue = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await queue.enqueue(task_id=t.id, from_agent="pm", to_agent="architect", message="m1")
    await queue.enqueue(task_id=t.id, from_agent="pm", to_agent="reviewer", message="m2")
    pending = await queue.pending()
    assert len(pending) == 2
    assert pending[0].message == "m1"
