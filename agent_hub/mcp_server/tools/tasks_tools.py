"""Tasks MCP tools — thin wrappers around TaskRepository.

Each tool's input is validated by FastMCP from its type annotations.
Errors from the repository (e.g. InvalidTransition) are caught and
returned as {"error": str} so the calling agent can self-correct on
the next turn.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.repository import TaskRepository


def _task_to_dict(task) -> dict:
    return {
        "id": task.id,
        "parent_id": task.parent_id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value,
        "owner": task.owner,
        "worktree_path": task.worktree_path,
        "branch_name": task.branch_name,
        "origin_chat_id": task.origin_chat_id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def _event_to_dict(ev) -> dict:
    return {
        "id": ev.id,
        "ts": ev.ts.isoformat(),
        "actor": ev.actor,
        "kind": ev.kind,
        "payload": ev.payload,
    }


def register(server: FastMCP, db_path: Path) -> None:
    repo = TaskRepository(db_path)

    @server.tool(name="tasks.create")
    async def tasks_create(
        title: str,
        description: str,
        origin_chat_id: int,
        parent_id: int | None = None,
        owner: str | None = None,
    ) -> dict:
        """Create a new task in 'pending' status. Returns the created task."""
        t = await repo.create(
            title=title, description=description, origin_chat_id=origin_chat_id,
            parent_id=parent_id, owner=owner,
        )
        return _task_to_dict(t)

    @server.tool(name="tasks.get")
    async def tasks_get(task_id: int) -> dict:
        """Returns the task and its 20 most recent events. {"error": ...} if unknown."""
        t = await repo.get(task_id)
        if t is None:
            return {"error": f"Unknown task {task_id}"}
        events = await repo.events(task_id, limit=20)
        return {"task": _task_to_dict(t), "recent_events": [_event_to_dict(e) for e in events]}

    @server.tool(name="tasks.list")
    async def tasks_list(
        status: str | None = None,
        owner: str | None = None,
        parent_id: int | None = None,
    ) -> list[dict]:
        """List tasks, optionally filtered by status/owner/parent_id."""
        status_enum = TaskStatus(status) if status else None
        tasks = await repo.list(status=status_enum, owner=owner, parent_id=parent_id)
        return [_task_to_dict(t) for t in tasks]

    @server.tool(name="tasks.tree")
    async def tasks_tree(task_id: int) -> dict:
        """Returns root + all descendants. {"error": ...} if root unknown."""
        result = await repo.tree(task_id)
        if result is None:
            return {"error": f"Unknown task {task_id}"}
        return {
            "root": _task_to_dict(result["root"]),
            "descendants": [_task_to_dict(t) for t in result["descendants"]],
        }

    @server.tool(name="tasks.update")
    async def tasks_update(
        task_id: int,
        status: str | None = None,
        owner: str | None = None,
        worktree_path: str | None = None,
        branch_name: str | None = None,
    ) -> dict:
        """Update task fields. Status changes validated against the transition map."""
        try:
            status_enum = TaskStatus(status) if status else None
            t = await repo.update(
                task_id,
                status=status_enum,
                owner=owner,
                worktree_path=worktree_path,
                branch_name=branch_name,
            )
        except InvalidTransition as exc:
            return {"error": str(exc)}
        if t is None:
            return {"error": f"Unknown task {task_id}"}
        return _task_to_dict(t)

    @server.tool(name="tasks.comment")
    async def tasks_comment(task_id: int, body: str, actor: str = "agent") -> dict:
        """Append a comment event to the task. Returns the new event_id."""
        event_id = await repo.comment(task_id, actor=actor, body=body)
        return {"event_id": event_id}
