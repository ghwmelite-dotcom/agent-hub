"""Test the memory.note MCP tool — the project_fact escape hatch."""

from __future__ import annotations

import pytest

from agent_hub.db import Database
from agent_hub.memory.store import MemoryStore


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return temp_db_path


@pytest.mark.asyncio
async def test_memory_note_inserts_project_fact(db_path, monkeypatch, tmp_path):
    from agent_hub.mcp_server.tools import memory_tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AGENT_HUB_DB", str(db_path))
    monkeypatch.setenv("AGENT_HUB_WORKSPACE", str(workspace))

    result = await memory_tools.memory_note(
        type="project_fact",
        title="Build cmd is npm run build:prod",
        body="The package.json has it under build:prod, not build.",
    )
    assert result.get("ok") is True

    rows = await MemoryStore(db_path).list(
        workspace=str(workspace), type="project_fact",
    )
    assert len(rows) == 1
    assert rows[0]["title"] == "Build cmd is npm run build:prod"
    assert rows[0]["agent_source"] is not None


@pytest.mark.asyncio
async def test_memory_note_rejects_non_project_fact(db_path, monkeypatch, tmp_path):
    from agent_hub.mcp_server.tools import memory_tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AGENT_HUB_DB", str(db_path))
    monkeypatch.setenv("AGENT_HUB_WORKSPACE", str(workspace))

    result = await memory_tools.memory_note(
        type="lesson",
        title="X", body="b",
    )
    assert result.get("ok") is False
    assert "project_fact" in result.get("error", "").lower()


@pytest.mark.asyncio
async def test_memory_note_missing_env_returns_error(db_path, monkeypatch):
    from agent_hub.mcp_server.tools import memory_tools

    monkeypatch.delenv("AGENT_HUB_DB", raising=False)
    monkeypatch.delenv("AGENT_HUB_WORKSPACE", raising=False)

    result = await memory_tools.memory_note(
        type="project_fact", title="X", body="b",
    )
    assert result.get("ok") is False
