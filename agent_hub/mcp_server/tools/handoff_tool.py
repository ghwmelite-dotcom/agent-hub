"""Handoff MCP tool — enqueues an agent-to-agent dispatch.

The orchestrator (later plan) pops these and routes them to the
target agent's session, prepending task context.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from agent_hub.tasks.handoff_queue import HandoffQueue


def register(server: FastMCP, db_path: Path) -> None:
    queue = HandoffQueue(db_path)

    @server.tool(name="handoff")
    async def handoff(to_agent: str, task_id: int, message: str, from_agent: str) -> dict:
        """Enqueue a handoff to another agent. Returns the queue_id.

        Self-handoff is disallowed — pass to a different agent or
        keep working in the current turn.
        """
        if to_agent == from_agent:
            return {"error": f"Cannot hand off to self ({from_agent}). Keep working or pass to a different agent."}
        qid = await queue.enqueue(
            task_id=task_id, from_agent=from_agent,
            to_agent=to_agent, message=message,
        )
        return {"enqueued": True, "queue_id": qid}
