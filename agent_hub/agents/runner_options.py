"""Pure helpers that build the ClaudeSDKClient option payload.

Separated from runner.py so they can be tested without instantiating
the SDK client (which would spawn a subprocess).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from agent_hub.agents.registry import AgentRole


def build_mcp_server_config(db_path: Path, cwd: Path | None = None) -> dict[str, Any]:
    """The stdio launch spec passed to ClaudeAgentOptions.mcp_servers.

    Keyed under "agent_hub" so MCP tool names land in the
    "mcp__agent_hub__*" namespace.

    Env handling: the Claude Code CLI uses the `env` dict as the SOLE
    environment for the subprocess (it does NOT inherit the parent env).
    On Windows that breaks anything that imports asyncio (WinError 10106
    from `_overlapped`) because SystemRoot / PATH / etc. are missing.

    PYTHONPATH: the CLI launches the subprocess from its own cwd, so
    `agent_hub` isn't on sys.path unless we add the project root. We
    derive the root from this file's location (.../agent_hub/agents/
    runner_options.py) — parents[2] is the repo root.

    `cwd`: when provided, sets AGENT_HUB_WORKSPACE in the subprocess env
    so the memory.note MCP tool knows which workspace to scope facts to.
    """
    project_root = Path(__file__).resolve().parents[2]
    existing_pp = os.environ.get("PYTHONPATH", "")
    python_path = (
        f"{project_root}{os.pathsep}{existing_pp}" if existing_pp else str(project_root)
    )
    env: dict[str, str] = {
        **os.environ,
        "AGENT_HUB_DB": str(db_path),
        "PYTHONPATH": python_path,
    }
    if cwd is not None:
        env["AGENT_HUB_WORKSPACE"] = str(cwd)
    return {
        "agent_hub": {
            "command": sys.executable,
            "args": ["-m", "agent_hub.mcp_server"],
            "env": env,
        },
    }


async def build_sdk_options(
    role: AgentRole,
    *,
    cwd: Path | None,
    db_path: Path,
    session_id: str | None = None,
) -> Any:
    """Construct a ClaudeAgentOptions for the given role + workspace.

    If `cwd` is set, loads project memory for that workspace+role and
    appends a `## Project memory` section to the role's system prompt.

    `session_id` (when set) pins the conversation to a known UUID so a
    later reconnect can pick up where it left off — the Claude Code CLI
    persists conversation history per session_id. Pass the value
    returned by AgentSessionStore.get_or_create.
    """
    import claude_agent_sdk as sdk

    system_prompt = role.system_prompt
    if cwd is not None:
        from agent_hub.memory.store import MemoryStore

        memory_section = await MemoryStore(db_path).load_for_prompt(
            workspace=str(cwd), agent_name=role.name,
        )
        if memory_section:
            system_prompt = f"{system_prompt}\n\n{memory_section}"

    # Isolation: the SDK is designed for Claude Code, so by default it
    # exposes Claude Code's full toolset AND loads the user's CLAUDE.md /
    # skills configs. Both pollute our agent's behavior — agents prefer
    # familiar Claude Code tools (TaskCreate, Agent subagent dispatcher,
    # etc.) over our mcp__agent_hub__* tools. Forcing isolation:
    #   tools=<builtins_only>   — explicit list of BUILT-IN tools only.
    #                              MCP tools come in via mcp_servers, NOT here.
    #                              Including mcp__* names in `tools=` makes
    #                              the SDK reject MCP calls as "tool not
    #                              available" because it interprets `tools=`
    #                              as a strict built-in allowlist.
    #   setting_sources=[]      — skip user/project/local config (no CLAUDE.md).
    #   skills=[]               — skip global skill loading.
    #   allowed_tools           — broader allowlist (built-in + MCP names) for
    #                              permission gating.
    builtin_tools = [t for t in role.allowed_tools if not t.startswith("mcp__")]
    kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "tools": builtin_tools,
        "allowed_tools": role.allowed_tools,
        "setting_sources": [],
        "skills": [],
        "model": role.model,
        "cwd": str(cwd) if cwd else None,
        "mcp_servers": build_mcp_server_config(db_path, cwd=cwd),
    }
    if session_id is not None:
        kwargs["session_id"] = session_id
    return sdk.ClaudeAgentOptions(**kwargs)
