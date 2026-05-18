import pytest
from mcp.server.fastmcp import FastMCP

from agent_hub.db import Database
from agent_hub.mcp_server.tools.gate_tools import register
from agent_hub.mcp_server.tools.tasks_tools import register as register_tasks
from agent_hub.tasks.gates import GateRepository


@pytest.fixture
async def server_and_gates(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    server = FastMCP("test")
    register_tasks(server, temp_db_path)
    register(server, temp_db_path)
    return server, GateRepository(temp_db_path)


def _tool(server: FastMCP, name: str):
    return server._tool_manager.get_tool(name).fn


@pytest.mark.asyncio
async def test_gate_request_creates_pending(server_and_gates):
    server, gates = server_and_gates
    create = _tool(server, "tasks.create")
    gate_request = _tool(server, "gate.request")
    gate_status = _tool(server, "gate.status")
    t = await create(title="x", description="-", origin_chat_id=1)
    res = await gate_request(task_id=t["id"], kind="design", summary="please review")
    assert res["gate_id"] > 0
    s = await gate_status(task_id=t["id"], kind="design")
    assert s["status"] == "pending"


@pytest.mark.asyncio
async def test_gate_status_none_when_no_gate(server_and_gates):
    server, _ = server_and_gates
    create = _tool(server, "tasks.create")
    gate_status = _tool(server, "gate.status")
    t = await create(title="x", description="-", origin_chat_id=1)
    s = await gate_status(task_id=t["id"], kind="design")
    assert s["status"] == "none"
