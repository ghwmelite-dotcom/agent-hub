"""Direct user→agent path tests for Orchestrator.handle().

The handoff queue path is covered by tests/integration; this file
focuses on the inline `handle()` entrypoint that the Telegram bot
uses for fresh user messages.
"""

from __future__ import annotations

import pytest

from agent_hub.agents.registry import AgentRegistry
from agent_hub.db import Database
from agent_hub.orchestrator.router import Orchestrator
from tests.fakes.fake_runner import FakeAgentRunner, scripted_turn


@pytest.fixture
async def orch(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    registry = AgentRegistry.load()
    runner = FakeAgentRunner()
    return Orchestrator(
        registry=registry,
        runner=runner,
        db=db,
        surface=None,
        repo_root=None,
    ), runner


@pytest.mark.asyncio
async def test_handle_prepends_chat_id_for_pm(orch):
    """PM needs origin_chat_id to file fresh tasks. handle() injects it."""
    orchestrator, runner = orch
    runner.script("pm", task_id=None, events=scripted_turn(text="ok"))

    async for _ in orchestrator.handle(chat_id=12345, message="please add X"):
        pass

    # FakeAgentRunner records (agent, message, task_id)
    assert len(runner.calls) == 1
    agent, message, task_id = runner.calls[0]
    assert agent == "pm"
    assert message == "[chat_id=12345] please add X"
    assert task_id is None


@pytest.mark.asyncio
async def test_handle_does_not_prepend_chat_id_for_other_agents(orch):
    """Architect/implementer/etc. read task context from the [task #N]
    prefix that _tick_handoff adds. handle() should not add chat_id noise
    when the user @-mentions them directly."""
    orchestrator, runner = orch
    runner.script("architect", task_id=None, events=scripted_turn(text="hm"))

    async for _ in orchestrator.handle(chat_id=12345, message="@architect thoughts?"):
        pass

    assert len(runner.calls) == 1
    agent, message, task_id = runner.calls[0]
    assert agent == "architect"
    assert message == "thoughts?"
    assert "chat_id" not in message


@pytest.mark.asyncio
async def test_handle_prepends_for_sticky_pm(orch):
    """When sticky returns PM, the chat_id injection still fires."""
    orchestrator, runner = orch
    orchestrator.set_sticky(12345, "pm")
    runner.script("pm", task_id=None, events=scripted_turn(text="ok"))

    async for _ in orchestrator.handle(chat_id=12345, message="anything"):
        pass

    agent, message, _ = runner.calls[0]
    assert agent == "pm"
    assert message == "[chat_id=12345] anything"
