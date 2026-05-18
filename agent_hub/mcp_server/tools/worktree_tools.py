"""Worktree MCP tools — read-only lookup of a task's recorded worktree path."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from agent_hub.mcp_server.tools._safe import safe_tool
from agent_hub.tasks.worktree_repo import WorktreeRepository


def register(server: FastMCP, db_path: Path) -> None:
    repo = WorktreeRepository(db_path)

    @server.tool(name="worktree.path")
    @safe_tool
    async def worktree_path(task_id: int) -> dict:
        """Return the recorded worktree path for a task, or {"error": ...} if none.

        Agents call this at the start of a turn to confirm their cwd
        matches the task's worktree. If the result's `path` differs
        from the agent's actual cwd, the runner/worktree setup is
        broken and the agent should stop and report.
        """
        row = await repo.get_by_task(task_id)
        if row is None:
            return {"error": f"No worktree recorded for task {task_id}"}
        if row.cleaned_at is not None:
            return {
                "error": (
                    f"Worktree for task {task_id} was cleaned at "
                    f"{row.cleaned_at.isoformat()}"
                )
            }
        return {
            "path": row.path,
            "branch": row.branch,
            "base_branch": row.base_branch,
        }
