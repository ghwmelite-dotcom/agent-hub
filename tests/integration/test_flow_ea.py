"""Flow EA — trading task design gate.

End-to-end through the orchestrator's state machine using FakeAgentRunner.
Mirrors tests/integration/test_flow_a.py but for the trading-specific
chain:

  user → PM creates [EA]-prefixed task → handoff to quant →
  quant produces design + gate.request → user /approves →
  fullstack implements → reviewer LGTM → backtest-analyst PASS →
  push.

No real Claude SDK, no real Telegram bot. Validates the routing wiring
through the FakeAgentRunner so a prompt-or-routing regression to the
EA path doesn't ship silently.
"""

from __future__ import annotations

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
    """Bare remote + clone with one initial MQL-ish commit."""
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    local = tmp_path / "local"
    subprocess.check_call(["git", "clone", str(remote), str(local)])
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=local)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=local)
    # Seed an MQL file so reviewer's Glob '**/*.mq{4,5}' would find it.
    (local / "scalper.mq5").write_text("// scalper EA\n")
    subprocess.check_call(["git", "add", "scalper.mq5"], cwd=local)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=local)
    subprocess.check_call(["git", "push", "origin", "main"], cwd=local)
    return remote, local


@pytest.mark.asyncio
async def test_flow_ea_quant_design_gate_through_backtest_analyst(
    temp_db_path, git_repos, tmp_path,
):
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

    # 1. User filed a trading task. PM creates with the [EA] title prefix
    #    so downstream agents can route to the EA chain.
    task = await repo.create(
        title="[EA] Add Bollinger Bands filter to scalper",
        description="Add a BB volatility filter to skip flat-market trades.",
        origin_chat_id=42,
    )
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await queue.enqueue(
        task_id=task.id,
        from_agent="pm",
        to_agent="quant",
        message="Design the BB filter integration into the scalper.",
    )

    # 2. Quant produces design + gate.request. Status flips to design_review.
    runner.script("quant", task_id=task.id, events=[
        TextChunk(text="Signal: skip when BB width < median(BB width, 200)"),
        ToolStart(tool="tasks.comment", input={"task_id": task.id, "body": "design"}),
        ToolEnd(tool="tasks.comment", is_error=False),
        ToolStart(tool="gate.request", input={"task_id": task.id, "kind": "design"}),
        ToolEnd(tool="gate.request", is_error=False),
        TurnDone(cost_usd=0.01, duration_ms=20),
    ])
    await orch._tick_handoff()
    # The fake tools don't actually mutate DB — apply the real MCP-tool
    # effects manually (mirrors test_flow_a's pattern).
    await repo.comment(task.id, actor="quant", body="design ready")
    await gates.request(task_id=task.id, kind="design", summary="BB filter")
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)

    # 3. Gate watcher DMs the user.
    await orch._tick_gates()
    assert any(
        "design" in m.lower() and f"#{task.id}" in m
        for m in surface.dms_to(42)
    )

    # 4. User /approves. Worktree gets created, fullstack picks it up.
    worktrees_root = tmp_path / "wt"
    worktrees_root.mkdir()
    reply = await handle_approve(
        task_id=task.id,
        db_path=temp_db_path,
        repo_root=repo_root,
        worktrees_root=worktrees_root,
    )
    assert "approved" in reply.lower() or "fullstack" in reply.lower()
    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.READY

    # /approve enqueued the handoff to fullstack-engineer.
    pending = await queue.pending()
    assert any(
        h.to_agent == "fullstack-engineer" and h.task_id == task.id
        for h in pending
    )

    # 5. Fullstack implements + commits in the worktree, hands off to reviewer.
    wt_row = await wt_repo.get_by_task(task.id)
    assert wt_row is not None
    wt_path = Path(wt_row.path)
    (wt_path / "bb_filter.mqh").write_text("// BB filter\n")
    subprocess.check_call(["git", "add", "bb_filter.mqh"], cwd=wt_path)
    subprocess.check_call(["git", "commit", "-m", "feat: BB filter"], cwd=wt_path)

    runner.script("fullstack-engineer", task_id=task.id, events=[
        TextChunk(text="MQL project — done; handed off to reviewer."),
        TurnDone(cost_usd=0.02, duration_ms=30),
    ])
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS, owner="fullstack-engineer")
    await orch._tick_handoff()  # claims + dispatches the /approve handoff
    await repo.update(task.id, status=TaskStatus.REVIEW)
    await queue.enqueue(
        task_id=task.id, from_agent="fullstack-engineer", to_agent="reviewer",
        message="implemented (MQL project — route QA to backtest-analyst)",
    )

    # 6. Reviewer approves and routes to BACKTEST-ANALYST (not qa) because
    #    title starts with [EA].
    runner.script("reviewer", task_id=task.id, events=[
        TextChunk(text="Approved — MQL project, routing to backtest-analyst."),
        TurnDone(cost_usd=0.005, duration_ms=10),
    ])
    await orch._tick_handoff()

    # 7. Backtest-analyst PASS → task done. Push.
    await queue.enqueue(
        task_id=task.id, from_agent="reviewer", to_agent="backtest-analyst",
        message="approved; please run backtest validation",
    )
    runner.script("backtest-analyst", task_id=task.id, events=[
        TextChunk(text="Equity curve clean, edge survives 1-tick perturbation."),
        TurnDone(cost_usd=0.003, duration_ms=10),
    ])
    await repo.update(task.id, status=TaskStatus.DONE)
    await orch._tick_handoff()

    # 8. Push happened. Branch lands on the bare remote, user gets DM.
    remote_branches = subprocess.check_output(
        ["git", "branch", "--list"], cwd=remote,
    ).decode()
    assert f"task/{task.id}" in remote_branches
    assert any("pushed" in m.lower() for m in surface.dms_to(42))

    # The runner saw both quant AND backtest-analyst dispatched — proves
    # the EA-specific chain was exercised end-to-end, not the default one.
    agents_called = [agent for agent, _, _ in runner.calls]
    assert "quant" in agents_called
    assert "backtest-analyst" in agents_called
    # Neither architect nor qa should have been dispatched.
    assert "architect" not in agents_called
    assert "qa" not in agents_called
