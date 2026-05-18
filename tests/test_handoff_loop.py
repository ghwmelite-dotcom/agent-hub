"""Tests for the orchestrator's handoff loop — claims handoff_queue rows
and dispatches them to the runner with task context attached."""

import asyncio

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.agents.runner import TextChunk, TurnDone
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    runner = FakeAgentRunner()
    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=surface,
    )
    return orch, runner, surface, TaskRepository(temp_db_path), HandoffQueue(temp_db_path)


@pytest.mark.asyncio
async def test_tick_dispatches_one_handoff(deps):
    orch, runner, surface, repo, queue = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await queue.enqueue(task_id=task.id, from_agent="pm", to_agent="architect", message="design this")

    runner.script("architect", task_id=task.id, events=[
        TextChunk(text="ok"),
        TurnDone(cost_usd=0.01, duration_ms=10),
    ])

    await orch._tick_handoff()

    assert runner.calls == [("architect", _expected_routed_message(task.id, "pm", "design this"), task.id)]


@pytest.mark.asyncio
async def test_tick_no_op_when_queue_empty(deps):
    orch, runner, _, _, _ = deps
    await orch._tick_handoff()
    assert runner.calls == []


def _expected_routed_message(task_id: int, from_agent: str, body: str) -> str:
    """The orchestrator prepends task context. Match the exact format the
    impl produces — adjust this helper if the format changes."""
    return f"[task #{task_id}, from @{from_agent}] {body}"
