"""Tests for push-on-done — when a task transitions to done, push the
branch to origin and DM the user."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from agent_hub.db import Database
from agent_hub.orchestrator.push import push_task_branch
from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository


@pytest.fixture
def git_repos(tmp_path: Path) -> tuple[Path, Path]:
    """Create a bare 'remote' and a clone of it with one commit on main."""
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])

    local = tmp_path / "local"
    subprocess.check_call(["git", "clone", str(remote), str(local)])
    subprocess.check_call(["git", "config", "user.name", "Test"], cwd=local)
    subprocess.check_call(["git", "config", "user.email", "t@example.com"], cwd=local)
    (local / "README.md").write_text("hi\n")
    subprocess.check_call(["git", "add", "README.md"], cwd=local)
    subprocess.check_call(["git", "commit", "-m", "initial"], cwd=local)
    subprocess.check_call(["git", "push", "origin", "main"], cwd=local)
    return remote, local


@pytest.mark.asyncio
async def test_push_pushes_branch_to_origin(git_repos, temp_db_path, tmp_path):
    remote, local = git_repos
    worktrees_root = tmp_path / "wt"
    worktrees_root.mkdir()
    wt_path = worktrees_root / "1"
    subprocess.check_call(
        ["git", "worktree", "add", "-b", "task/1-x", str(wt_path), "main"],
        cwd=local,
    )
    (wt_path / "a.txt").write_text("a\n")
    subprocess.check_call(["git", "add", "a.txt"], cwd=wt_path)
    subprocess.check_call(["git", "commit", "-m", "work"], cwd=wt_path)

    db = Database(temp_db_path)
    await db.init()
    repo = TaskRepository(temp_db_path)
    wt_repo = WorktreeRepository(temp_db_path)
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await wt_repo.record(
        task_id=task.id, path=str(wt_path), branch="task/1-x", base_branch="main",
    )

    result = await push_task_branch(task_id=task.id, repo_root=local, db_path=temp_db_path)

    assert result["pushed"] is True
    assert result["branch"] == "task/1-x"
    branches = subprocess.check_output(
        ["git", "branch", "--list"], cwd=remote,
    ).decode()
    assert "task/1-x" in branches


@pytest.mark.asyncio
async def test_push_unknown_task_returns_error(temp_db_path, tmp_path):
    db = Database(temp_db_path)
    await db.init()
    result = await push_task_branch(
        task_id=99999, repo_root=tmp_path, db_path=temp_db_path,
    )
    assert result["pushed"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_push_no_worktree_returns_error(temp_db_path, tmp_path):
    db = Database(temp_db_path)
    await db.init()
    repo = TaskRepository(temp_db_path)
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    result = await push_task_branch(
        task_id=task.id, repo_root=tmp_path, db_path=temp_db_path,
    )
    assert result["pushed"] is False
    assert "error" in result
