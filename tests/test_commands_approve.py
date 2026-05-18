"""Tests for /approve <id> — resolves the pending design gate and
flips the task status to ready."""

import subprocess
from pathlib import Path

import pytest

from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository
from agent_hub.telegram_bot.commands.approve_cmd import handle_approve


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), GateRepository(temp_db_path), db


@pytest.fixture
def git_repos(tmp_path: Path):
    """Bare remote + local clone with one commit on main."""
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    local = tmp_path / "repo"
    subprocess.check_call(["git", "clone", str(remote), str(local)])
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=local)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=local)
    (local / "x.txt").write_text("x\n")
    subprocess.check_call(["git", "add", "x.txt"], cwd=local)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=local)
    subprocess.check_call(["git", "push", "origin", "main"], cwd=local)
    return local


@pytest.mark.asyncio
async def test_approve_resolves_pending_gate(deps):
    repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    reply = await handle_approve(task_id=task.id, db_path=db.path)

    assert await gates.status(task_id=task.id, kind="design") == "approved"
    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.READY
    assert "approved" in reply.lower() or f"#{task.id}" in reply


@pytest.mark.asyncio
async def test_approve_unknown_task_returns_error(deps):
    repo, _, db = deps
    reply = await handle_approve(task_id=99999, db_path=db.path)
    assert "not found" in reply.lower() or "unknown" in reply.lower()


@pytest.mark.asyncio
async def test_approve_with_no_pending_gate_reports_no_op(deps):
    repo, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    # Task is PENDING, no design gate requested yet.
    reply = await handle_approve(task_id=task.id, db_path=db.path)
    assert "no" in reply.lower() and "gate" in reply.lower()


@pytest.mark.asyncio
async def test_approve_with_repo_root_creates_worktree_and_handoff(deps, git_repos, tmp_path):
    repo, gates, db = deps
    task = await repo.create(title="add health", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    worktrees_root = tmp_path / "worktrees"

    reply = await handle_approve(
        task_id=task.id,
        db_path=db.path,
        repo_root=git_repos,
        worktrees_root=worktrees_root,
    )

    # Gate resolved, status flipped to READY
    assert await gates.status(task_id=task.id, kind="design") == "approved"
    assert (await repo.get(task.id)).status == TaskStatus.READY

    # Worktree created and recorded
    wt_repo = WorktreeRepository(db.path)
    wt_row = await wt_repo.get_by_task(task.id)
    assert wt_row is not None
    assert Path(wt_row.path).exists()
    assert wt_row.branch == f"task/{task.id}-add-health"

    # Handoff to fullstack-engineer enqueued
    queue = HandoffQueue(db.path)
    pending = await queue.pending()
    assert any(h.to_agent == "fullstack-engineer" and h.task_id == task.id for h in pending)

    # Reply mentions the next step
    assert "fullstack" in reply.lower() or "ready" in reply.lower()


@pytest.mark.asyncio
async def test_approve_without_repo_root_is_gate_only(deps):
    """Backward-compat path: no repo_root means just resolve gate + flip status."""
    repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    reply = await handle_approve(task_id=task.id, db_path=db.path)

    assert (await repo.get(task.id)).status == TaskStatus.READY
    assert "approved" in reply.lower()
