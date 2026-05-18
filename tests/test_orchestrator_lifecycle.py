"""Tests for Orchestrator.start/stop lifecycle of background tasks."""

import asyncio

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
async def orchestrator(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=FakeMessageSurface(),
    )


@pytest.mark.asyncio
async def test_start_then_stop_terminates_cleanly(orchestrator):
    await orchestrator.start()
    # Give the loops a couple of ticks to spin up.
    await asyncio.sleep(0.05)
    await orchestrator.stop()
    # After stop, the background tasks should be done.
    for task in orchestrator._tasks:
        assert task.done()


@pytest.mark.asyncio
async def test_stop_without_start_is_noop(orchestrator):
    """Calling stop on a never-started orchestrator must not raise."""
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_double_start_raises(orchestrator):
    await orchestrator.start()
    try:
        with pytest.raises(RuntimeError):
            await orchestrator.start()
    finally:
        await orchestrator.stop()
