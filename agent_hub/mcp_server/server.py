"""Agent Hub MCP server — stdio transport, exposes orchestration tools.

Launched per-agent by the ClaudeSDKClient via:
  ClaudeAgentOptions(mcp_servers=[{"command": "python", "args": ["-m", "agent_hub.mcp_server"]}])

The server is stateless beyond the SQLite database it reads/writes.
DB path is resolved from the AGENT_HUB_DB env var (set by the host
process before launching the SDK client).
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP


def _resolve_db_path() -> Path:
    raw = os.environ.get("AGENT_HUB_DB")
    if not raw:
        raise RuntimeError(
            "AGENT_HUB_DB env var must be set when launching agent_hub.mcp_server"
        )
    return Path(raw)


def build_server() -> FastMCP:
    """Construct the FastMCP server with all tools registered.

    Kept as a function so tests can build a fresh server per case.
    """
    server = FastMCP("agent-hub")
    db_path = _resolve_db_path()

    # Tool registration happens in submodules so each tool family is
    # independently testable. Each register_X function takes the server
    # plus the db_path and binds tools that close over a repository.
    from agent_hub.mcp_server.tools.tasks_tools import register as register_tasks
    from agent_hub.mcp_server.tools.handoff_tool import register as register_handoff
    from agent_hub.mcp_server.tools.gate_tools import register as register_gate

    register_tasks(server, db_path)
    register_handoff(server, db_path)
    register_gate(server, db_path)
    return server
