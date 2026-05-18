"""Pure helpers that build the ClaudeSDKClient option payload.

Separated from runner.py so they can be tested without instantiating
the SDK client (which would spawn a subprocess).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agent_hub.agents.registry import AgentRole


def build_mcp_server_config(db_path: Path) -> dict[str, Any]:
    """The stdio launch spec passed to ClaudeAgentOptions.mcp_servers.

    Keyed under "agent_hub" so MCP tool names land in the
    "mcp__agent_hub__*" namespace.
    """
    return {
        "agent_hub": {
            "command": sys.executable,
            "args": ["-m", "agent_hub.mcp_server"],
            "env": {"AGENT_HUB_DB": str(db_path)},
        },
    }


def build_sdk_options(role: AgentRole, *, cwd: Path | None, db_path: Path) -> Any:
    """Construct a ClaudeAgentOptions for the given role + workspace.

    Returns the SDK's options object (whose exact class lives in
    claude_agent_sdk). Keeping the SDK import lazy here so test-time
    import of this module is cheap.
    """
    import claude_agent_sdk as sdk

    return sdk.ClaudeAgentOptions(
        system_prompt=role.system_prompt,
        allowed_tools=role.allowed_tools,
        model=role.model,
        cwd=str(cwd) if cwd else None,
        mcp_servers=build_mcp_server_config(db_path),
    )
