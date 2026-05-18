"""Flow C — design rejection loop.

Architect designs → gate.request → user /rejects with feedback →
status returns to planning → handoff to architect with the rejection
context. (We don't run the architect's second turn — the test asserts
the orchestrator state is correctly set up for it.)
"""

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.reject_cmd import handle_reject
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.mark.asyncio
async def test_flow_c_design_rejection(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface,
    )
    repo = TaskRepository(temp_db_path)
    queue = HandoffQueue(temp_db_path)
    gates = GateRepository(temp_db_path)

    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    await orch._tick_gates()  # user gets the design-ready DM

    reply = await handle_reject(
        task_id=task.id,
        reason="prefer SELECT 1 not full query",
        db_path=temp_db_path,
    )

    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.PLANNING
    assert await gates.status(task_id=task.id, kind="design") == "rejected"

    pending = await queue.pending()
    assert any(
        h.to_agent == "architect" and "SELECT 1" in h.message for h in pending
    )
    events = await repo.events(task.id)
    assert any("rejected" in (e.payload.get("body") or "").lower() for e in events)
    assert "rejected" in reply.lower()
