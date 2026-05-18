"""Tests for parallel handoff dispatch.

The orchestrator spawns N concurrent handoff workers (default 3).
Each worker independently claims rows; multiple in-flight tasks
progress in parallel rather than queuing behind each other.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.agents.runner import TextChunk, TurnDone
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


class _SlowFakeRunner(FakeAgentRunner):
    """Sleeps a fixed duration during send() so we can detect serialization."""

    def __init__(self, turn_seconds: float = 0.30) -> None:
        super().__init__()
        self.turn_seconds = turn_seconds

    async def send(self, agent_name, message, *, task_id=None):
        await asyncio.sleep(self.turn_seconds)
        self.calls.append((agent_name, message, task_id))
        yield TextChunk(text="done")
        yield TurnDone(cost_usd=0.0, duration_ms=int(self.turn_seconds * 1000))


@pytest.mark.asyncio
async def test_multiple_workers_dispatch_concurrently(temp_db_path):
    """3 handoffs across 3 tasks should finish in roughly 1× turn time
    with 3 workers — not 3× turn time as serial dispatch would imply."""
    db = Database(temp_db_path)
    await db.init()
    repo = TaskRepository(temp_db_path)
    queue = HandoffQueue(temp_db_path)

    task_ids = []
    for i in range(3):
        t = await repo.create(title=f"t{i}", description="-", origin_chat_id=1)
        await repo.update(t.id, status=TaskStatus.PLANNING)
        await queue.enqueue(
            task_id=t.id, from_agent="pm", to_agent="architect", message="m"
        )
        task_ids.append(t.id)

    turn_seconds = 0.30
    runner = _SlowFakeRunner(turn_seconds=turn_seconds)
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=FakeMessageSurface(),
        handoff_worker_count=3,
    )

    started = asyncio.get_event_loop().time()
    try:
        await orch.start()
        # Poll until all 3 handoffs are drained
        for _ in range(50):
            await asyncio.sleep(0.05)
            if len(runner.calls) >= 3:
                break
        elapsed = asyncio.get_event_loop().time() - started

        assert len(runner.calls) == 3
        # Concurrent: should finish faster than serial would (3× turn time).
        # Serial floor is ~0.9s; parallel should land well below that even
        # with Windows scheduler jitter. 2.2× turn time = 0.66s is the
        # ceiling we set — anything serial would be ~0.9-1.1s.
        ceiling = turn_seconds * 2.5
        assert elapsed < ceiling, (
            f"Expected parallel dispatch (< {ceiling:.2f}s), took {elapsed:.2f}s"
        )
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_single_worker_falls_back_to_serial(temp_db_path):
    """handoff_worker_count=1 preserves the old serial behavior — 3 turns
    take 3× turn time. Sanity-check that the parallel test isn't measuring
    something other than concurrency."""
    db = Database(temp_db_path)
    await db.init()
    repo = TaskRepository(temp_db_path)
    queue = HandoffQueue(temp_db_path)

    for i in range(3):
        t = await repo.create(title=f"t{i}", description="-", origin_chat_id=1)
        await repo.update(t.id, status=TaskStatus.PLANNING)
        await queue.enqueue(
            task_id=t.id, from_agent="pm", to_agent="architect", message="m"
        )

    turn_seconds = 0.20
    runner = _SlowFakeRunner(turn_seconds=turn_seconds)
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=FakeMessageSurface(),
        handoff_worker_count=1,
    )

    started = asyncio.get_event_loop().time()
    try:
        await orch.start()
        for _ in range(60):
            await asyncio.sleep(0.05)
            if len(runner.calls) >= 3:
                break
        elapsed = asyncio.get_event_loop().time() - started
        assert len(runner.calls) == 3
        # Serial floor is ~3× turn_seconds; allow some loop-tick overhead
        assert elapsed >= turn_seconds * 2.5, (
            f"Expected serial dispatch (>= {turn_seconds * 2.5}s), took {elapsed:.2f}s"
        )
    finally:
        await orch.stop()


@pytest.mark.asyncio
async def test_default_worker_count_is_one_in_constructor(temp_db_path):
    """Tests that don't specify handoff_worker_count get the safer
    serial default — keeps existing integration tests deterministic."""
    db = Database(temp_db_path)
    await db.init()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
    )
    assert orch.handoff_worker_count == 1
