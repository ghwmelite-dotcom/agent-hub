"""Tests for gate-timeout reminder DMs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
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
    return (
        TaskRepository(temp_db_path),
        GateRepository(temp_db_path),
        db,
    )


async def _backdate_gate_request(db_path, gate_id, *, hours: int):
    past = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE gates SET requested_at = ? WHERE id = ?",
            (past, gate_id),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_needing_reminder_returns_old_pending_gates(deps):
    repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    gate_id = await gates.request(task_id=task.id, kind="design")
    await gates.mark_notified(gate_id)
    await _backdate_gate_request(db.path, gate_id, hours=30)

    result = await gates.needing_reminder(timeout_hours=24)
    assert len(result) == 1
    assert result[0].id == gate_id


@pytest.mark.asyncio
async def test_needing_reminder_skips_fresh_gates(deps):
    repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    gate_id = await gates.request(task_id=task.id, kind="design")
    await gates.mark_notified(gate_id)
    # Don't backdate — gate is fresh

    result = await gates.needing_reminder(timeout_hours=24)
    assert result == []


@pytest.mark.asyncio
async def test_needing_reminder_skips_un_notified_gates(deps):
    """Gates that never got the initial DM are handled by `_tick_gates`'s
    first pass — reminders are only for already-announced gates."""
    repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    gate_id = await gates.request(task_id=task.id, kind="design")
    await _backdate_gate_request(db.path, gate_id, hours=30)
    # Note: NOT calling mark_notified

    result = await gates.needing_reminder(timeout_hours=24)
    assert result == []


@pytest.mark.asyncio
async def test_needing_reminder_respects_last_reminder_at(deps):
    """A gate that already got a reminder within the cooldown window is skipped."""
    repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    gate_id = await gates.request(task_id=task.id, kind="design")
    await gates.mark_notified(gate_id)
    await _backdate_gate_request(db.path, gate_id, hours=48)
    await gates.mark_reminder_sent(gate_id)  # just now

    result = await gates.needing_reminder(timeout_hours=24)
    assert result == []


@pytest.mark.asyncio
async def test_needing_reminder_re_fires_after_cooldown(deps):
    """If the last reminder was longer than the cooldown ago, fire again."""
    repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    gate_id = await gates.request(task_id=task.id, kind="design")
    await gates.mark_notified(gate_id)
    await _backdate_gate_request(db.path, gate_id, hours=72)
    # Backdate the prior reminder too
    past = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    async with aiosqlite.connect(db.path) as conn:
        await conn.execute(
            "UPDATE gates SET last_reminder_at = ? WHERE id = ?",
            (past, gate_id),
        )
        await conn.commit()

    result = await gates.needing_reminder(timeout_hours=24)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_tick_gates_sends_reminder_and_marks_it(deps):
    repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=99)
    gate_id = await gates.request(task_id=task.id, kind="design", summary="add /health")
    await gates.mark_notified(gate_id)
    await _backdate_gate_request(db.path, gate_id, hours=30)

    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface,
        gate_reminder_hours=24,
    )

    await orch._tick_gates()

    msgs = surface.dms_to(99)
    reminder_msgs = [m for m in msgs if "Reminder" in m]
    assert len(reminder_msgs) == 1
    assert "/approve" in reminder_msgs[0]

    # last_reminder_at recorded so the next tick is a no-op
    surface2 = FakeMessageSurface()
    orch2 = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface2,
        gate_reminder_hours=24,
    )
    await orch2._tick_gates()
    assert surface2.dms_to(99) == []  # no duplicate reminder
