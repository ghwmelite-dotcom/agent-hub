import pytest
from mcp.server.fastmcp import FastMCP

from agent_hub.db import Database
from agent_hub.mcp_server.tools.handoff_tool import register
from agent_hub.mcp_server.tools.tasks_tools import register as register_tasks
from agent_hub.tasks.handoff_queue import HandoffQueue


@pytest.fixture
async def server_and_queue(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    server = FastMCP("test")
    register_tasks(server, temp_db_path)  # need tasks.create to set up
    register(server, temp_db_path)
    return server, HandoffQueue(temp_db_path)


def _tool(server: FastMCP, name: str):
    return server._tool_manager.get_tool(name).fn


@pytest.mark.asyncio
async def test_handoff_enqueues_row(server_and_queue):
    server, queue = server_and_queue
    create = _tool(server, "tasks.create")
    handoff = _tool(server, "handoff")
    t = await create(title="x", description="-", origin_chat_id=1)
    result = await handoff(to_agent="architect", task_id=t["id"], message="design this", from_agent="pm")
    assert result["enqueued"] is True
    assert result["queue_id"] > 0

    pending = await queue.pending()
    assert len(pending) == 1
    assert pending[0].to_agent == "architect"
    assert pending[0].message == "design this"


@pytest.mark.asyncio
async def test_handoff_to_self_returns_error(server_and_queue):
    server, _ = server_and_queue
    create = _tool(server, "tasks.create")
    handoff = _tool(server, "handoff")
    t = await create(title="x", description="-", origin_chat_id=1)
    result = await handoff(to_agent="pm", task_id=t["id"], message="me", from_agent="pm")
    assert "error" in result
