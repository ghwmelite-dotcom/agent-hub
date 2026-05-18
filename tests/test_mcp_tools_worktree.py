"""Direct-call tests for the worktree.path MCP tool."""

import pytest
from mcp.server.fastmcp import FastMCP

from agent_hub.db import Database
from agent_hub.mcp_server.tools.worktree_tools import register
from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository


@pytest.fixture
async def server_and_db(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    server = FastMCP("test")
    register(server, temp_db_path)
    return server, temp_db_path


def _tool(server: FastMCP, name: str):
    return server._tool_manager.get_tool(name).fn


@pytest.mark.asyncio
async def test_worktree_path_returns_recorded(server_and_db):
    server, db_path = server_and_db
    repo = TaskRepository(db_path)
    wt_repo = WorktreeRepository(db_path)
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await wt_repo.record(
        task_id=t.id, path="/tmp/wt/x", branch="task/x", base_branch="main",
    )

    fn = _tool(server, "worktree.path")
    result = await fn(task_id=t.id)
    assert result["path"] == "/tmp/wt/x"
    assert result["branch"] == "task/x"
    assert result["base_branch"] == "main"


@pytest.mark.asyncio
async def test_worktree_path_unknown_returns_error(server_and_db):
    server, _ = server_and_db
    fn = _tool(server, "worktree.path")
    result = await fn(task_id=99999)
    assert "error" in result


@pytest.mark.asyncio
async def test_worktree_path_cleaned_returns_error(server_and_db):
    server, db_path = server_and_db
    repo = TaskRepository(db_path)
    wt_repo = WorktreeRepository(db_path)
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await wt_repo.record(
        task_id=t.id, path="/tmp/wt/x", branch="task/x", base_branch="main",
    )
    await wt_repo.mark_cleaned(t.id)

    fn = _tool(server, "worktree.path")
    result = await fn(task_id=t.id)
    assert "error" in result
    assert "cleaned" in result["error"].lower()
