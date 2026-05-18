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


import asyncio


@pytest.mark.asyncio
async def test_claim_returns_one_unclaimed(deps):
    repo, queue = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    qid = await queue.enqueue(task_id=t.id, from_agent="pm", to_agent="x", message="m")

    claimed = await queue.claim()
    assert claimed is not None
    assert claimed.id == qid
    assert claimed.claimed_at is not None


@pytest.mark.asyncio
async def test_claim_returns_none_when_empty(deps):
    repo, queue = deps
    assert await queue.claim() is None


@pytest.mark.asyncio
async def test_claim_skips_already_claimed(deps):
    repo, queue = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await queue.enqueue(task_id=t.id, from_agent="pm", to_agent="x", message="m")
    first = await queue.claim()
    assert first is not None
    second = await queue.claim()
    assert second is None


@pytest.mark.asyncio
async def test_concurrent_claim_one_winner(deps):
    """10 concurrent claim() calls against 1 row — exactly one wins, others get None."""
    repo, queue = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await queue.enqueue(task_id=t.id, from_agent="pm", to_agent="x", message="m")

    results = await asyncio.gather(*[queue.claim() for _ in range(10)])
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
