from agent_hub.worktree_manager import branch_slug


def test_branch_slug_simple_title():
    assert branch_slug(42, "add health endpoint") == "task/42-add-health-endpoint"


def test_branch_slug_lowercases():
    assert branch_slug(7, "Add Login") == "task/7-add-login"


def test_branch_slug_strips_special_chars():
    assert branch_slug(1, "Fix bug: @user/path!") == "task/1-fix-bug-user-path"


def test_branch_slug_collapses_whitespace():
    assert branch_slug(1, "  many   spaces  ") == "task/1-many-spaces"


def test_branch_slug_truncates_long_titles():
    long_title = "a" * 200
    slug = branch_slug(99, long_title)
    parts = slug.split("/", 1)
    assert parts[0] == "task"
    rest = parts[1]
    title_portion = rest.split("-", 1)[1]
    assert len(title_portion) <= 60


def test_branch_slug_unicode_falls_back_to_id():
    assert branch_slug(5, "🎉🎊") == "task/5"
    assert branch_slug(6, "") == "task/6"


import asyncio
import subprocess
from pathlib import Path

import pytest

from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository
from agent_hub.worktree_manager import WorktreeManager


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialise a fresh git repo with an initial commit on main."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "t@example.com"], cwd=repo)
    (repo / "README.md").write_text("hello\n")
    subprocess.check_call(["git", "add", "README.md"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "initial"], cwd=repo)
    return repo


@pytest.fixture
async def manager_deps(temp_db_path, git_repo, tmp_path):
    db = Database(temp_db_path)
    await db.init()
    worktrees_root = tmp_path / "worktrees"
    manager = WorktreeManager(
        repo_root=git_repo,
        worktrees_root=worktrees_root,
        db_path=temp_db_path,
    )
    repo = TaskRepository(temp_db_path)
    return manager, repo, worktrees_root


@pytest.mark.asyncio
async def test_create_makes_worktree_and_branch(manager_deps):
    manager, repo, worktrees_root = manager_deps
    task = await repo.create(title="add health", description="-", origin_chat_id=1)

    result = await manager.create(task_id=task.id, title=task.title, base_branch="main")

    assert result["branch"] == f"task/{task.id}-add-health"
    expected_path = worktrees_root / str(task.id)
    assert Path(result["path"]) == expected_path
    assert expected_path.exists()
    # README from main should be visible inside the worktree
    assert (expected_path / "README.md").exists()


@pytest.mark.asyncio
async def test_create_records_db_row(manager_deps):
    manager, repo, _ = manager_deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await manager.create(task_id=task.id, title=task.title, base_branch="main")

    from agent_hub.tasks.worktree_repo import WorktreeRepository
    wt_repo = WorktreeRepository(manager.db_path)
    row = await wt_repo.get_by_task(task.id)
    assert row is not None
    assert row.branch == f"task/{task.id}-x"
    assert row.cleaned_at is None
