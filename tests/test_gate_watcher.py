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
