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
