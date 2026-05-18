"""End-to-end smoke test with a real Haiku-pinned agent set.

Gated behind RUN_SMOKE_TESTS=1 because it:
- Makes real Claude API calls (~$0.10-0.50 per run)
- Takes 30s-2min to complete
- Requires ANTHROPIC_API_KEY

Run manually before tagging a release:
    set RUN_SMOKE_TESTS=1
    .\\.venv\\Scripts\\python.exe -m pytest tests/smoke/ -v -s
"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from agent_hub.agents import AgentRegistry, AgentRunner
from agent_hub.agents.registry import AgentRole
from agent_hub.config import Settings
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from tests.fakes.fake_surface import FakeMessageSurface


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SMOKE_TESTS") != "1",
    reason="set RUN_SMOKE_TESTS=1 to run smoke tests (real Claude API calls)",
)


def _haiku_pinned_registry(original: AgentRegistry) -> AgentRegistry:
    """Return a registry where every role's model is overridden to
    Haiku for the smoke run."""
    pinned = []
    for r in original.all():
        pinned.append(AgentRole(
            name=r.name,
            display_name=r.display_name,
            aliases=r.aliases,
            model="claude-haiku-4-5-20251001",
            allowed_tools=r.allowed_tools,
            system_prompt=r.system_prompt,
        ))
    return AgentRegistry(pinned)


def _seed_git_repo(repo_root: Path) -> None:
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo_root)
    subprocess.check_call(["git", "config", "user.name", "Smoke"], cwd=repo_root)
    subprocess.check_call(["git", "config", "user.email", "smoke@example.com"], cwd=repo_root)
    (repo_root / "README.md").write_text("# Smoke project\n\nA tiny test target.\n")
    subprocess.check_call(["git", "add", "README.md"], cwd=repo_root)
    subprocess.check_call(["git", "commit", "-m", "initial"], cwd=repo_root)


@pytest.mark.asyncio
async def test_haiku_end_to_end_simple_task(tmp_path: Path):
    """File a trivial task, watch PM -> architect -> /approve -> fullstack
    -> reviewer -> QA -> done. Uses FakeMessageSurface to capture DMs."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    repo_root = tmp_path / "smoke-project"
    repo_root.mkdir()
    _seed_git_repo(repo_root)
    worktrees_root = tmp_path / "worktrees"

    db_path = tmp_path / "agent_hub.db"
    os.environ["AGENT_HUB_DB"] = str(db_path)

    db = Database(db_path)
    await db.init()
    repo = TaskRepository(db_path)
    queue = HandoffQueue(db_path)

    settings = Settings(
        telegram_bot_token="smoke-dummy",
        telegram_allowed_user_id=1,
        database_path=db_path,
        agent_workspaces=[repo_root],
    )

    registry = _haiku_pinned_registry(AgentRegistry.load())
    runner = AgentRunner(settings=settings, registry=registry)
    surface = FakeMessageSurface()

    orch = Orchestrator(
        registry=registry,
        runner=runner,
        db=db,
        surface=surface,
        repo_root=repo_root,
    )

    try:
        await orch.start()

        task = await repo.create(
            title="Add hello line to README",
            description=(
                "Append a single line `Hello agent team.` to README.md "
                "in this repo. This is a smoke test - keep the change minimal."
            ),
            origin_chat_id=999,
        )
        await queue.enqueue(
            task_id=task.id, from_agent="user", to_agent="pm",
            message=f"User filed task #{task.id}: append 'Hello agent team.' to README.md",
        )

        # Poll up to 4 min for done. Auto-approve any pending design gate.
        deadline = asyncio.get_event_loop().time() + 240
        final_status = None
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2.0)
            current = await repo.get(task.id)
            if current is None:
                continue
            if current.status == TaskStatus.DONE:
                final_status = TaskStatus.DONE
                break
            if current.status == TaskStatus.BLOCKED:
                final_status = TaskStatus.BLOCKED
                break

            from agent_hub.tasks.gates import GateRepository
            from agent_hub.telegram_bot.commands.approve_cmd import handle_approve
            gates = GateRepository(db_path)
            gate_status = await gates.status(task_id=task.id, kind="design")
            if gate_status == "pending":
                await handle_approve(
                    task_id=task.id,
                    db_path=db_path,
                    repo_root=repo_root,
                    worktrees_root=worktrees_root,
                )

        assert final_status == TaskStatus.DONE, (
            f"Task did not reach done; final status: {final_status}. "
            f"DMs captured: {[m for _, m in surface.sent]}"
        )

        proc = subprocess.run(
            ["git", "branch", "--list"],
            cwd=repo_root,
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        assert f"task/{task.id}" in proc.stdout, (
            f"No task branch found in git output: {proc.stdout}"
        )

    finally:
        await orch.stop()
        await runner.shutdown()
        os.environ.pop("AGENT_HUB_DB", None)
