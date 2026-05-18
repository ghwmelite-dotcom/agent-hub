"""Tests for stuck-task detection in the gate-watcher loop."""

from __future__ import annotations

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return (
        TaskRepository(temp_db_path),
        HandoffQueue(temp_db_path),
        db,
    )


@pytest.mark.asyncio
async def test_turns_since_status_change_counts_all_when_never_changed(deps):
    repo, queue, _ = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    for _ in range(5):
        await queue.enqueue(
            task_id=task.id, from_agent="pm", to_agent="architect", message="m"
        )

    assert await repo.turns_since_status_change(task.id) == 5


@pytest.mark.asyncio
async def test_turns_since_status_change_resets_on_status_change(deps):
    repo, queue, _ = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    for _ in range(3):
        await queue.enqueue(
            task_id=task.id, from_agent="pm", to_agent="architect", message="m"
        )

    # Status change resets the count window
    await repo.update(task.id, status=TaskStatus.PLANNING)
    for _ in range(2):
        await queue.enqueue(
            task_id=task.id, from_agent="pm", to_agent="architect", message="m"
        )

    assert await repo.turns_since_status_change(task.id) == 2


@pytest.mark.asyncio
async def test_tick_stuck_tasks_dms_user_above_threshold(deps):
    repo, queue, db = deps
    task = await repo.create(title="big feature", description="-", origin_chat_id=77)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    for _ in range(15):
        await queue.enqueue(
            task_id=task.id, from_agent="pm", to_agent="architect", message="m"
        )

    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface,
        stuck_turn_threshold=12,
    )

    await orch._tick_stuck_tasks()

    msgs = surface.dms_to(77)
    assert len(msgs) == 1
    assert "stuck" in msgs[0].lower()
    assert f"#{task.id}" in msgs[0]


@pytest.mark.asyncio
async def test_tick_stuck_tasks_idempotent_until_status_changes(deps):
    repo, queue, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=77)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    for _ in range(15):
        await queue.enqueue(
            task_id=task.id, from_agent="pm", to_agent="architect", message="m"
        )

    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface,
        stuck_turn_threshold=12,
    )

    await orch._tick_stuck_tasks()
    first = list(surface.dms_to(77))
    assert len(first) == 1

    # Second tick: no new DM (the stuck_alert event is in the log)
    await orch._tick_stuck_tasks()
    assert surface.dms_to(77) == first

    # After a status change, the count resets — we'd need to exceed
    # the threshold again before another DM fires.
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await orch._tick_stuck_tasks()
    assert surface.dms_to(77) == first  # turns_since_status_change is now 0


@pytest.mark.asyncio
async def test_tick_stuck_tasks_skips_terminal_status(deps):
    repo, queue, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=77)
    # Move through to done
    for s in (
        TaskStatus.PLANNING, TaskStatus.DESIGN_REVIEW, TaskStatus.READY,
        TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE,
    ):
        await repo.update(task.id, status=s)
    # Many handoffs after done shouldn't trigger
    for _ in range(20):
        await queue.enqueue(
            task_id=task.id, from_agent="pm", to_agent="architect", message="m"
        )

    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface,
        stuck_turn_threshold=12,
    )

    await orch._tick_stuck_tasks()
    assert surface.dms_to(77) == []


@pytest.mark.asyncio
async def test_tick_stuck_tasks_under_threshold_silent(deps):
    repo, queue, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=77)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    for _ in range(5):
        await queue.enqueue(
            task_id=task.id, from_agent="pm", to_agent="architect", message="m"
        )

    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface,
        stuck_turn_threshold=12,
    )

    await orch._tick_stuck_tasks()
    assert surface.dms_to(77) == []
