"""Flow A — single feature with design gate.

End-to-end through the orchestrator's state machine using FakeAgentRunner.
No real Claude SDK, no real Telegram bot.

Walks through:
  user → PM creates task → handoff to architect →
  architect produces design + gate.request → user /approves →
  fullstack implements → reviewer LGTM → QA done → push.

This test exercises the orchestrator's tick loops directly; it does
NOT spin up the full background tasks because we want deterministic
ordering.
"""

import subprocess
from pathlib import Path

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.agents.runner import TextChunk, ToolEnd, ToolStart, TurnDone
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository
from agent_hub.telegram_bot.commands.approve_cmd import handle_approve
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
def git_repos(tmp_path: Path):
    """Bare remote + clone with one initial commit. Returns (remote, local)."""
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    local = tmp_path / "local"
    subprocess.check_call(["git", "clone", str(remote), str(local)])
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=local)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=local)
    (local / "x.txt").write_text("x\n")
    subprocess.check_call(["git", "add", "x.txt"], cwd=local)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=local)
    subprocess.check_call(["git", "push", "origin", "main"], cwd=local)
    return remote, local


@pytest.mark.asyncio
async def test_flow_a_single_feature_design_gate(temp_db_path, git_repos, tmp_path):
    remote, repo_root = git_repos

    db = Database(temp_db_path)
    await db.init()
    runner = FakeAgentRunner()
    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=surface,
        repo_root=repo_root,
    )
    repo = TaskRepository(temp_db_path)
    queue = HandoffQueue(temp_db_path)
    gates = GateRepository(temp_db_path)
    wt_repo = WorktreeRepository(temp_db_path)

    # 1. User filed task via PM (we apply the DB state directly — Plan 4
    #    is where the PM prompt actually drives this).
    task = await repo.create(title="add /health", description="ping D1", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="Design /health",
    )

    # 2. Architect script: produces design and requests gate.
    runner.script("architect", task_id=task.id, events=[
        TextChunk(text="Design: SELECT 1 against D1, 5s timeout."),
        ToolStart(tool="tasks.comment", input={"task_id": task.id, "body": "design"}),
        ToolEnd(tool="tasks.comment", is_error=False),
        ToolStart(tool="gate.request", input={"task_id": task.id, "kind": "design"}),
        ToolEnd(tool="gate.request", is_error=False),
        TurnDone(cost_usd=0.01, duration_ms=20),
    ])
    # Tools in the script don't actually mutate DB (they're fakes), so we
    # apply the corresponding effects manually — this is what the real
    # MCP tools would do.
    await orch._tick_handoff()
    await repo.comment(task.id, actor="architect", body="design ready")
    await gates.request(task_id=task.id, kind="design", summary="ready")
    # Status is already DESIGN_REVIEW from step 1; no additional update needed.

    # 3. Gate watcher DMs the user.
    await orch._tick_gates()
    assert any("design" in m.lower() and f"#{task.id}" in m for m in surface.dms_to(42))

    # 4. User /approves.
    reply = await handle_approve(task_id=task.id, db_path=temp_db_path)
    assert "approved" in reply.lower()
    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.READY

    # 5. Approval would now trigger a handoff to fullstack — we apply
    #    that effect explicitly here (Plan 4 wires this into /approve).
    worktrees_root = tmp_path / "wt"
    worktrees_root.mkdir()
    wt_path = worktrees_root / str(task.id)
    subprocess.check_call(
        ["git", "worktree", "add", "-b", f"task/{task.id}-health", str(wt_path), "main"],
        cwd=repo_root,
    )
    (wt_path / "health.py").write_text("# health\n")
    subprocess.check_call(["git", "add", "health.py"], cwd=wt_path)
    subprocess.check_call(["git", "commit", "-m", "feat: health"], cwd=wt_path)
    await wt_repo.record(
        task_id=task.id, path=str(wt_path),
        branch=f"task/{task.id}-health", base_branch="main",
    )
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS)
    await repo.update(task.id, owner="fullstack-engineer")

    # 6. Fullstack hands off to reviewer.
    await queue.enqueue(
        task_id=task.id, from_agent="fullstack-engineer", to_agent="reviewer",
        message="implemented",
    )
    runner.script("reviewer", task_id=task.id, events=[
        TextChunk(text="LGTM"),
        TurnDone(cost_usd=0.005, duration_ms=10),
    ])
    await orch._tick_handoff()

    # 7. Reviewer hands off to QA.
    await repo.update(task.id, status=TaskStatus.REVIEW)
    await queue.enqueue(
        task_id=task.id, from_agent="reviewer", to_agent="qa", message="approved",
    )
    runner.script("qa", task_id=task.id, events=[
        TextChunk(text="tests pass"),
        TurnDone(cost_usd=0.003, duration_ms=10),
    ])
    # Apply the QA tool effect (status to done) before the tick.
    await repo.update(task.id, status=TaskStatus.DONE)
    await orch._tick_handoff()

    # 8. Push should have happened. Check the branch lands on the remote.
    remote_branches = subprocess.check_output(
        ["git", "branch", "--list"], cwd=remote,
    ).decode()
    assert f"task/{task.id}-health" in remote_branches
    assert any("pushed" in m.lower() for m in surface.dms_to(42))
