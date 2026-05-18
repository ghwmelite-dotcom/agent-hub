"""End-to-end stdio test: spawn the MCP server as a subprocess and
exercise a real tool call through the official client.
"""

import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_hub.db import Database


@pytest.mark.asyncio
async def test_e2e_create_task_via_mcp(temp_db_path):
    db = Database(temp_db_path)
    await db.init()

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_hub.mcp_server"],
        env={**os.environ, "AGENT_HUB_DB": str(temp_db_path)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "tasks.create" in names
            assert "handoff" in names
            assert "gate.request" in names

            # origin_chat_id is declared as `str` on the tool so models can
            # safely pass stringified numerics. The tool coerces to int.
            result = await session.call_tool(
                "tasks.create",
                {"title": "e2e", "description": "-", "origin_chat_id": "1"},
            )
            # FastMCP wraps the return in result.content[0].text as JSON.
            payload = json.loads(result.content[0].text)
            assert payload["id"] > 0
            assert payload["status"] == "pending"
