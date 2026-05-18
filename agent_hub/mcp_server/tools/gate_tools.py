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
from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.repository import TaskRepository


def register(server: FastMCP, db_path: Path) -> None:
    gates = GateRepository(db_path)
    tasks = TaskRepository(db_path)

    @server.tool(name="gate.request")
    @safe_tool
    async def gate_request(
        task_id: int,
        kind: str = "design",
        artifact_path: str | None = None,
        summary: str | None = None,
    ) -> dict:
        """Request a human gate. Pauses the task until the user resolves.

        Side effect: atomically transitions the task to `design_review`.
        That removes a sequencing footgun where the architect requests
        the gate but forgets the followup `tasks.update(design_review)`,
        leaving `/approve` unable to advance from `planning → ready`.
        Idempotent in practice — if the task is already in
        `design_review` we leave it alone.
        """
        if kind != "design":
            return {"error": f"Unknown gate kind {kind!r}. v1 supports only 'design'."}
        gid = await gates.request(
            task_id=task_id, kind=kind,
            artifact_path=artifact_path, summary=summary,
        )
        current = await tasks.get(task_id)
        if current is not None and current.status != TaskStatus.DESIGN_REVIEW:
            try:
                await tasks.update(task_id, status=TaskStatus.DESIGN_REVIEW)
            except InvalidTransition:
                # Task is in a state from which DESIGN_REVIEW isn't reachable
                # (e.g. already past). The gate is still created so the user
                # can react; we surface this to the caller.
                return {
                    "gate_id": gid,
                    "warning": (
                        f"Gate created but task status is {current.status.value!r}; "
                        f"could not advance to design_review."
                    ),
                }
        return {"gate_id": gid}

    @server.tool(name="gate.status")
    @safe_tool
    async def gate_status(task_id: int, kind: str = "design") -> dict:
        """Returns {'status': 'pending'|'approved'|'rejected'|'none'}."""
        s = await gates.status(task_id=task_id, kind=kind)
        return {"status": s}
