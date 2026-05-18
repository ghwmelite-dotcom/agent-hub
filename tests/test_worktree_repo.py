import pytest

from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), WorktreeRepository(temp_db_path)


@pytest.mark.asyncio
async def test_record_inserts_row(deps):
    repo, worktrees = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await worktrees.record(
        task_id=t.id,
        path="/tmp/wt/42",
        branch="task/42-x",
        base_branch="main",
    )
    row = await worktrees.get_by_task(t.id)
    assert row is not None
    assert row.path == "/tmp/wt/42"
    assert row.branch == "task/42-x"
    assert row.base_branch == "main"
    assert row.cleaned_at is None


@pytest.mark.asyncio
async def test_get_by_task_returns_none_when_no_row(deps):
    repo, worktrees = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    assert await worktrees.get_by_task(t.id) is None


@pytest.mark.asyncio
async def test_mark_cleaned_sets_timestamp(deps):
    repo, worktrees = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await worktrees.record(task_id=t.id, path="/tmp/wt/42", branch="task/42-x", base_branch="main")
    await worktrees.mark_cleaned(t.id)
    row = await worktrees.get_by_task(t.id)
    assert row is not None
    assert row.cleaned_at is not None


@pytest.mark.asyncio
async def test_list_active_excludes_cleaned(deps):
    repo, worktrees = deps
    t1 = await repo.create(title="a", description="-", origin_chat_id=1)
    t2 = await repo.create(title="b", description="-", origin_chat_id=1)
    await worktrees.record(task_id=t1.id, path="/tmp/wt/1", branch="task/1-a", base_branch="main")
    await worktrees.record(task_id=t2.id, path="/tmp/wt/2", branch="task/2-b", base_branch="main")
    await worktrees.mark_cleaned(t2.id)

    active = await worktrees.list_active()
    active_ids = {w.task_id for w in active}
    assert active_ids == {t1.id}


@pytest.mark.asyncio
async def test_mark_cleaned_idempotent(deps):
    repo, worktrees = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await worktrees.record(task_id=t.id, path="/tmp/wt/42", branch="task/42-x", base_branch="main")
    await worktrees.mark_cleaned(t.id)
    first_cleaned_at = (await worktrees.get_by_task(t.id)).cleaned_at
    # Second call should not change the timestamp.
    await worktrees.mark_cleaned(t.id)
    second_cleaned_at = (await worktrees.get_by_task(t.id)).cleaned_at
    assert first_cleaned_at == second_cleaned_at


from pathlib import Path


@pytest.mark.asyncio
async def test_list_orphans_returns_rows_with_missing_dirs(deps, tmp_path):
    repo, worktrees = deps
    t1 = await repo.create(title="alive", description="-", origin_chat_id=1)
    t2 = await repo.create(title="orphan", description="-", origin_chat_id=1)

    alive_path = tmp_path / "alive"
    alive_path.mkdir()
    orphan_path = tmp_path / "orphan-does-not-exist"  # deliberately not created

    await worktrees.record(task_id=t1.id, path=str(alive_path), branch="task/1-alive", base_branch="main")
    await worktrees.record(task_id=t2.id, path=str(orphan_path), branch="task/2-orphan", base_branch="main")

    orphans = await worktrees.list_orphans()
    assert {o.task_id for o in orphans} == {t2.id}


@pytest.mark.asyncio
async def test_list_orphans_excludes_cleaned_rows(deps, tmp_path):
    repo, worktrees = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    missing = tmp_path / "nope"
    await worktrees.record(task_id=t.id, path=str(missing), branch="task/x", base_branch="main")
    await worktrees.mark_cleaned(t.id)

    orphans = await worktrees.list_orphans()
    assert orphans == []
