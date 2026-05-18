"""Tests for the helper that builds the orchestrator and registers
its lifecycle with the PTB application."""

import pytest

from agent_hub.__main__ import _build_orchestrator
from agent_hub.config import Settings


def _settings(tmp_path):
    return Settings(
        telegram_bot_token="dummy",
        telegram_allowed_user_id=1,
        database_path=tmp_path / "agent_hub.db",
    )


@pytest.mark.asyncio
async def test_build_orchestrator_returns_orchestrator(tmp_path):
    settings = _settings(tmp_path)
    from agent_hub.agents import AgentRegistry
    from agent_hub.db import Database
    from agent_hub.agents.runner import AgentRunner

    db = Database(settings.database_path)
    await db.init()

    runner = AgentRunner(settings=settings, registry=AgentRegistry.load())
    orch = _build_orchestrator(
        settings=settings,
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=None,
    )
    assert orch is not None
    # repo_root should reflect settings.default_workspace (None if no workspaces)
    assert orch.repo_root == settings.default_workspace
