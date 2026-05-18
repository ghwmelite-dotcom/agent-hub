"""Tests for the gate watcher — DMs the user when a new design gate
is pending."""

import asyncio

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.repository import TaskRepository
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface,
    )
    return orch, surface, TaskRepository(temp_db_path), GateRepository(temp_db_path)


@pytest.mark.asyncio
async def test_tick_dms_user_on_new_pending_gate(deps):
    orch, surface, repo, gates = deps
    task = await repo.create(title="add /health", description="-", origin_chat_id=77)
    await gates.request(task_id=task.id, kind="design", summary="design ready")

    await orch._tick_gates()

    msgs = surface.dms_to(77)
    assert any(f"#{task.id}" in m for m in msgs)
    assert any("design" in m.lower() for m in msgs)
    assert any("/approve" in m or "approve" in m.lower() for m in msgs)


@pytest.mark.asyncio
async def test_tick_does_not_dm_same_gate_twice(deps):
    orch, surface, repo, gates = deps
    task = await repo.create(title="x", description="-", origin_chat_id=77)
    await gates.request(task_id=task.id, kind="design")

    await orch._tick_gates()
    first_count = len(surface.sent)
    await orch._tick_gates()
    second_count = len(surface.sent)

    assert first_count == 1
    assert second_count == 1  # no new DM on the second tick


@pytest.mark.asyncio
async def test_tick_ignores_resolved_gates(deps):
    orch, surface, repo, gates = deps
    task = await repo.create(title="x", description="-", origin_chat_id=77)
    await gates.request(task_id=task.id, kind="design")
    await gates.resolve(task_id=task.id, kind="design", resolution="approved")

    await orch._tick_gates()

    assert surface.sent == []


@pytest.mark.asyncio
async def test_start_releases_stale_claims_and_records_count(temp_db_path):
    """Orchestrator.start() must release claims left by a dead process and
    expose the count on `released_stale_claims` so the boot DM can show it."""
    from agent_hub.state_machine import TaskStatus
    from agent_hub.tasks.handoff_queue import HandoffQueue
    from tests.fakes.fake_runner import scripted_turn

    db = Database(temp_db_path)
    await db.init()
    repo = TaskRepository(temp_db_path)
    queue = HandoffQueue(temp_db_path)

    # Seed an active task whose handoff was claimed by a previous (dead)
    # process — the row is in the DB with claimed_at != NULL.
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="m"
    )
    await queue.claim()

    # Pre-script the agent — the handoff loop will pick the released row
    # up almost immediately, and we don't care about asserting "still
    # pending" (the release happens, the loop drains it — both fine).
    runner = FakeAgentRunner()
    runner.script("architect", task_id=task.id, events=scripted_turn(text="ok"))

    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=FakeMessageSurface(),
    )
    try:
        await orch.start()
        assert orch.released_stale_claims == 1
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_notified_state_persists_across_orchestrator_lifetime(temp_db_path):
    """Gate notified flag must survive a fresh Orchestrator instance.

    Simulates a process restart: tick the gate once with orch1 (DMs the
    user), then build a brand-new orch2 with the same DB and tick again.
    Pre-fix, the in-memory set was lost on restart so the user got a
    second DM. With the persisted column, orch2 sees `notified_at IS NOT
    NULL` and stays silent.
    """
    db = Database(temp_db_path)
    await db.init()
    repo = TaskRepository(temp_db_path)
    gates = GateRepository(temp_db_path)
    surface1 = FakeMessageSurface()

    orch1 = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface1,
    )
    task = await repo.create(title="add /health", description="-", origin_chat_id=99)
    await gates.request(task_id=task.id, kind="design")
    await orch1._tick_gates()
    assert len(surface1.sent) == 1  # First instance DMs

    # Simulate restart: brand-new orchestrator, brand-new surface
    surface2 = FakeMessageSurface()
    orch2 = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface2,
    )
    await orch2._tick_gates()
    assert surface2.sent == [], "Restart re-DM'd a gate that was already announced"


@pytest.mark.asyncio
async def test_loop_picks_up_new_gates(deps):
    orch, surface, repo, gates = deps
    await orch.start()
    try:
        task = await repo.create(title="x", description="-", origin_chat_id=77)
        await gates.request(task_id=task.id, kind="design")
        for _ in range(20):
            await asyncio.sleep(0.1)
            if surface.sent:
                break
        assert any(f"#{task.id}" in m for _, m in surface.sent)
    finally:
        await orch.stop()
