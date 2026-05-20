"""MCP tool: memory.note — record a project_fact.

The tool reads $AGENT_HUB_DB and $AGENT_HUB_WORKSPACE from the environment
so it is workspace-aware at call time (each agent subprocess is launched
with the appropriate workspace already in its env).

Only `type=project_fact` is accepted; lessons/preferences/decisions are
auto-captured by agent_hub.memory.capture and must not be written directly
by agents.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

from agent_hub.memory.store import MemoryStore

log = structlog.get_logger(__name__)


async def memory_note(
    *,
    type: str,
    title: str,
    body: str,
    agent_source: str | None = None,
) -> dict[str, Any]:
    """Record a project-level fact.

    `type` MUST be `project_fact` in MVP. Lesson/preference/decision are
    written only via auto-capture (see agent_hub.memory.capture).

    Reads the active workspace from $AGENT_HUB_WORKSPACE (set by the
    MCP server launcher; matches the pattern used by other tools).
    """
    if type != "project_fact":
        return {
            "ok": False,
            "error": "memory.note can only record type=project_fact; "
                     "other types are auto-captured.",
        }

    db_env = os.environ.get("AGENT_HUB_DB")
    ws_env = os.environ.get("AGENT_HUB_WORKSPACE")
    if not db_env or not ws_env:
        return {"ok": False, "error": "AGENT_HUB_DB or AGENT_HUB_WORKSPACE unset"}

    try:
        new_id = await MemoryStore(Path(db_env)).insert(
            workspace=ws_env,
            type="project_fact",
            agent_source=agent_source or "agent",
            title=title.strip()[:80],
            body=body.strip(),
        )
        return {"ok": True, "id": new_id}
    except Exception as exc:  # noqa: BLE001
        log.exception("memory.note.failed")
        return {"ok": False, "error": str(exc)}


def register(server: Any, db_path: Path) -> None:  # noqa: ARG001
    """Register memory.* tools with the FastMCP server.

    `db_path` is accepted to match the standard register() signature used
    by all other tool modules; the tool itself resolves the DB path from
    $AGENT_HUB_DB at call time so that it picks up the per-invocation env.
    """
    @server.tool(name="memory.note")
    async def memory__note(type: str, title: str, body: str) -> dict[str, Any]:
        """Record a project-level fact (build commands, stack, conventions).

        Only type='project_fact' is accepted. Other memory types (lesson,
        preference, decision) are captured automatically by the orchestrator.
        """
        return await memory_note(type=type, title=title, body=body)
