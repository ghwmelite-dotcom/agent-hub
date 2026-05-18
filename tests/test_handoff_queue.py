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


@pytest.mark.asyncio
async def test_release_stale_claims_recovers_active_task_rows(deps):
    """A row claimed by a previous (dead) process should be re-claimable
    on the next startup if its task is still active."""
    from agent_hub.state_machine import TaskStatus
    repo, queue = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(t.id, status=TaskStatus.PLANNING)
    await queue.enqueue(task_id=t.id, from_agent="pm", to_agent="architect", message="m")

    claimed = await queue.claim()
    assert claimed is not None
    assert await queue.claim() is None  # nothing left

    released = await queue.release_stale_claims()
    assert released == 1

    again = await queue.claim()
    assert again is not None
    assert again.id == claimed.id


@pytest.mark.asyncio
async def test_release_stale_claims_skips_done_and_blocked_tasks(deps):
    """Rows belonging to terminal-status tasks are audit logs — never replay them."""
    from agent_hub.state_machine import TaskStatus
    repo, queue = deps

    # Task 1: was completed normally — claimed row is historical
    done_task = await repo.create(title="done", description="-", origin_chat_id=1)
    await repo.update(done_task.id, status=TaskStatus.PLANNING)
    await repo.update(done_task.id, status=TaskStatus.DESIGN_REVIEW)
    await repo.update(done_task.id, status=TaskStatus.READY)
    await repo.update(done_task.id, status=TaskStatus.IN_PROGRESS)
    await repo.update(done_task.id, status=TaskStatus.REVIEW)
    await repo.update(done_task.id, status=TaskStatus.DONE)
    await queue.enqueue(
        task_id=done_task.id, from_agent="pm", to_agent="qa", message="legacy"
    )
    await queue.claim()

    # Task 2: cancelled — also historical
    blocked_task = await repo.create(title="blocked", description="-", origin_chat_id=1)
    await repo.update(blocked_task.id, status=TaskStatus.BLOCKED)
    await queue.enqueue(
        task_id=blocked_task.id, from_agent="pm", to_agent="architect", message="legacy"
    )
    await queue.claim()

    # Task 3: active — should be released
    active_task = await repo.create(title="active", description="-", origin_chat_id=1)
    await repo.update(active_task.id, status=TaskStatus.PLANNING)
    await queue.enqueue(
        task_id=active_task.id, from_agent="pm", to_agent="architect", message="real"
    )
    await queue.claim()

    released = await queue.release_stale_claims()
    assert released == 1  # only the active-task row

    re_claimed = await queue.claim()
    assert re_claimed is not None
    assert re_claimed.task_id == active_task.id


@pytest.mark.asyncio
async def test_release_stale_claims_no_op_when_nothing_claimed(deps):
    repo, queue = deps
    released = await queue.release_stale_claims()
    assert released == 0
