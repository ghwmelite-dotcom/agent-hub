"""Tests for the orchestrator's done-handling: when a turn results in
a task transitioning to done, the orchestrator runs push + epic +
worktree cleanup."""

import subprocess
from pathlib import Path

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.agents.runner import TextChunk, ToolEnd, ToolStart, TurnDone
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
def git_repos(tmp_path: Path):
    """Bare remote + clone with one initial commit."""
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    local = tmp_path / "local"
    subprocess.check_call(["git", "clone", str(remote), str(local)])
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=local)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=local)
    (local / "r.md").write_text("r\n")
    subprocess.check_call(["git", "add", "r.md"], cwd=local)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=local)
    subprocess.check_call(["git", "push", "origin", "main"], cwd=local)
    return remote, local


@pytest.fixture
async def deps_with_repo(temp_db_path, git_repos):
    remote, local = git_repos
    db = Database(temp_db_path)
    await db.init()
    runner = FakeAgentRunner()
    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=surface,
        repo_root=local,
    )
    repo = TaskRepository(temp_db_path)
    queue = HandoffQueue(temp_db_path)
    wt_repo = WorktreeRepository(temp_db_path)
    return orch, runner, surface, repo, queue, wt_repo, local


@pytest.mark.asyncio
async def test_done_transition_triggers_push_and_dm(deps_with_repo, tmp_path):
    orch, runner, surface, repo, queue, wt_repo, local = deps_with_repo

    task = await repo.create(title="x", description="-", origin_chat_id=99)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS)
    await repo.update(task.id, status=TaskStatus.REVIEW)

    worktrees_root = local.parent / "wt"
    worktrees_root.mkdir(exist_ok=True)
    wt_path = worktrees_root / str(task.id)
    subprocess.check_call(
        ["git", "worktree", "add", "-b", "task/x", str(wt_path), "main"],
        cwd=local,
    )
    (wt_path / "a.txt").write_text("a\n")
    subprocess.check_call(["git", "add", "a.txt"], cwd=wt_path)
    subprocess.check_call(["git", "commit", "-m", "w"], cwd=wt_path)
    await wt_repo.record(
        task_id=task.id, path=str(wt_path), branch="task/x", base_branch="main",
    )

    await queue.enqueue(task_id=task.id, from_agent="reviewer", to_agent="qa", message="approved")
    runner.script("qa", task_id=task.id, events=[
        TextChunk(text="tests pass"),
        TurnDone(cost_usd=0.01, duration_ms=10),
    ])

    # Apply the QA "tasks.update(status=done)" effect before the tick
    # (the real MCP tool would do this; the fake runner doesn't).
    await repo.update(task.id, status=TaskStatus.DONE)

    await orch._tick_handoff()

    # Branch should be on the remote
    remote_branches = subprocess.check_output(
        ["git", "branch", "--list"], cwd=local.parent / "remote.git",
    ).decode()
    assert "task/x" in remote_branches

    # User got DM'd
    msgs = surface.dms_to(99)
    assert any("pushed" in m.lower() or "task/x" in m for m in msgs)
