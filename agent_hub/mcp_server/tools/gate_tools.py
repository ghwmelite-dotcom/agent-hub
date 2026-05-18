"""Gate MCP tools — design approval lifecycle.

In v1 the only `kind` is "design". The architect calls gate.request
at the end of a design session; the orchestrator (later plan) sees
the pending row and DMs the user. The user's /approve or /reject
command resolves the gate from the orchestrator side — agents don't
resolve their own gates.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from agent_hub.mcp_server.tools._safe import safe_tool
from agent_hub.tasks.gates import GateRepository


def register(server: FastMCP, db_path: Path) -> None:
    gates = GateRepository(db_path)

    @server.tool(name="gate.request")
    @safe_tool
    async def gate_request(
        task_id: int,
        kind: str = "design",
        artifact_path: str | None = None,
        summary: str | None = None,
    ) -> dict:
        """Request a human gate. Pauses the task until the user resolves."""
        if kind != "design":
            return {"error": f"Unknown gate kind {kind!r}. v1 supports only 'design'."}
        gid = await gates.request(
            task_id=task_id, kind=kind,
            artifact_path=artifact_path, summary=summary,
        )
        return {"gate_id": gid}

    @server.tool(name="gate.status")
    @safe_tool
    async def gate_status(task_id: int, kind: str = "design") -> dict:
        """Returns {'status': 'pending'|'approved'|'rejected'|'none'}."""
        s = await gates.status(task_id=task_id, kind=kind)
        return {"status": s}
