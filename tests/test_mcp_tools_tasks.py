"""Direct-call tests for the tasks.* tool functions.

We don't spin up the MCP server here — we call the underlying handler
functions registered on a FastMCP instance and assert on the DB state.
"""

import pytest
from mcp.server.fastmcp import FastMCP

from agent_hub.db import Database
from agent_hub.mcp_server.tools.tasks_tools import register
from agent_hub.state_machine import TaskStatus


@pytest.fixture
async def server_and_db(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    server = FastMCP("test")
    register(server, temp_db_path)
    return server, temp_db_path


def _tool(server: FastMCP, name: str):
    """Look up a registered tool's underlying async function."""
    tools = server._tool_manager.list_tools()
    for t in tools:
        if t.name == name:
            return server._tool_manager.get_tool(name).fn
    raise KeyError(f"tool {name!r} not registered")


@pytest.mark.asyncio
async def test_tasks_create(server_and_db):
    server, _ = server_and_db
    fn = _tool(server, "tasks.create")
    result = await fn(title="x", description="y", origin_chat_id=1)
    assert result["id"] > 0
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_tasks_get(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    get = _tool(server, "tasks.get")
    created = await create(title="x", description="y", origin_chat_id=1)
    got = await get(task_id=created["id"])
    assert got["task"]["id"] == created["id"]
    assert got["recent_events"] == []


@pytest.mark.asyncio
async def test_tasks_list_filters(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    lst = _tool(server, "tasks.list")
    await create(title="a", description="-", origin_chat_id=1)
    await create(title="b", description="-", origin_chat_id=1)
    pending = await lst(status="pending")
    assert len(pending) == 2


@pytest.mark.asyncio
async def test_tasks_update_status(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    update = _tool(server, "tasks.update")
    t = await create(title="x", description="-", origin_chat_id=1)
    # pending -> planning is valid
    result = await update(task_id=t["id"], status="planning")
    assert result["status"] == "planning"


@pytest.mark.asyncio
async def test_tasks_update_invalid_status_returns_error(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    update = _tool(server, "tasks.update")
    t = await create(title="x", description="-", origin_chat_id=1)
    result = await update(task_id=t["id"], status="done")  # pending->done invalid
    assert "error" in result
    assert "Invalid" in result["error"] or "transition" in result["error"].lower()


@pytest.mark.asyncio
async def test_tasks_comment(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    comment = _tool(server, "tasks.comment")
    get = _tool(server, "tasks.get")
    t = await create(title="x", description="-", origin_chat_id=1)
    event_id = await comment(task_id=t["id"], body="filed it")
    assert event_id["event_id"] > 0
    detail = await get(task_id=t["id"])
    assert detail["recent_events"][-1]["payload"]["body"] == "filed it"


@pytest.mark.asyncio
async def test_tasks_tree(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    tree = _tool(server, "tasks.tree")
    epic = await create(title="epic", description="-", origin_chat_id=1)
    leaf = await create(title="leaf", description="-", origin_chat_id=1, parent_id=epic["id"])
    result = await tree(task_id=epic["id"])
    assert result["root"]["id"] == epic["id"]
    assert [d["id"] for d in result["descendants"]] == [leaf["id"]]
