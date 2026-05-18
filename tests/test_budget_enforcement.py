"""Tests for budget cap enforcement in Orchestrator._tick_handoff."""

from __future__ import annotations

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.budget_cmd import set_budget_cap, clear_budget_cap
from tests.fakes.fake_runner import FakeAgentRunner, scripted_turn
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return (
        db,
        TaskRepository(temp_db_path),
        HandoffQueue(temp_db_path),
    )


def _make_orch(db: Database, runner: FakeAgentRunner, surface: FakeMessageSurface) -> Orchestrator:
    return Orchestrator(
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=surface,
    )


@pytest.mark.asyncio
async def test_no_cap_allows_dispatch(deps):
    db, repo, queue = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="m"
    )
    runner = FakeAgentRunner()
    runner.script("architect", task_id=task.id, events=scripted_turn(text="ok"))
    surface = FakeMessageSurface()
    orch = _make_orch(db, runner, surface)

    await orch._tick_handoff()

    # Without a cap, the handoff was claimed and dispatched
    assert len(runner.calls) == 1
    assert (await queue.pending()) == []


@pytest.mark.asyncio
async def test_cap_exceeded_blocks_dispatch_and_dms_once(deps):
    db, repo, queue = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.add_cost(task.id, 6.50)
    await set_budget_cap(db.path, 5.00)
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="m"
    )
    runner = FakeAgentRunner()
    surface = FakeMessageSurface()
    orch = _make_orch(db, runner, surface)

    # First tick: cap exceeded, no dispatch, one DM
    await orch._tick_handoff()
    assert runner.calls == []  # nothing dispatched
    msgs1 = surface.dms_to(42)
    assert len(msgs1) == 1
    assert "$5.00" in msgs1[0]
    assert "paused" in msgs1[0].lower()

    # Second tick: still over cap, but no second DM
    await orch._tick_handoff()
    assert runner.calls == []
    assert surface.dms_to(42) == msgs1  # unchanged


@pytest.mark.asyncio
async def test_raising_cap_resets_dm_flag_and_resumes_dispatch(deps):
    db, repo, queue = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.add_cost(task.id, 6.50)
    await set_budget_cap(db.path, 5.00)
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="m"
    )
    runner = FakeAgentRunner()
    runner.script("architect", task_id=task.id, events=scripted_turn(text="ok"))
    surface = FakeMessageSurface()
    orch = _make_orch(db, runner, surface)

    # Cap exceeded, dispatch paused
    await orch._tick_handoff()
    assert runner.calls == []

    # User raises the cap so we're now under
    await set_budget_cap(db.path, 100.00)
    await orch._tick_handoff()

    # Dispatch fires
    assert len(runner.calls) == 1
    # _cap_dm_sent reset to False so a future excursion would DM again
    assert orch._cap_dm_sent is False


@pytest.mark.asyncio
async def test_cap_off_unblocks(deps):
    db, repo, queue = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.add_cost(task.id, 6.50)
    await set_budget_cap(db.path, 5.00)
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="m"
    )
    runner = FakeAgentRunner()
    runner.script("architect", task_id=task.id, events=scripted_turn(text="ok"))
    surface = FakeMessageSurface()
    orch = _make_orch(db, runner, surface)

    await orch._tick_handoff()  # paused
    await clear_budget_cap(db.path)
    await orch._tick_handoff()  # resumes

    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_per_turn_cost_accumulates_during_dispatch(deps):
    """When a TurnDone event carries cost_usd, the task's cost_usd_total
    grows by exactly that amount."""
    from agent_hub.agents.runner import TurnDone
    db, repo, queue = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="m"
    )

    runner = FakeAgentRunner()
    runner.script(
        "architect",
        task_id=task.id,
        events=[TurnDone(cost_usd=0.42, duration_ms=100)],
    )
    surface = FakeMessageSurface()
    orch = _make_orch(db, runner, surface)

    await orch._tick_handoff()

    fresh = await repo.get(task.id)
    assert abs(fresh.cost_usd_total - 0.42) < 1e-9
