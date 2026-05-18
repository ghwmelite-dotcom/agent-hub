"""Pure handler for /task <id> — task detail + 20 most-recent events."""

from __future__ import annotations

from pathlib import Path

from agent_hub.tasks.repository import TaskRepository


async def handle_task(*, task_id: int, db_path: Path) -> str:
    repo = TaskRepository(db_path)
    task = await repo.get(task_id)
    if task is None:
        return f"Task #{task_id} not found."

    events = await repo.events(task_id, limit=20)

    owner_str = f" (owner: @{task.owner})" if task.owner else ""
    lines = [
        f"*Task #{task.id}* — {task.title}",
        f"Status: {task.status.value}{owner_str}",
        f"Created: {task.created_at.isoformat()}",
        "",
        "Recent events:",
    ]
    if not events:
        lines.append("  (none)")
    else:
        for ev in events:
            ts = ev.ts.isoformat(timespec="seconds")
            body = _format_event_body(ev.kind, ev.payload)
            lines.append(f"  {ts} @{ev.actor} {ev.kind}: {body}")
    return "\n".join(lines)


def _format_event_body(kind: str, payload: dict) -> str:
    if kind == "comment":
        return str(payload.get("body", ""))[:200]
    if kind == "status_change":
        return f"{payload.get('from')} → {payload.get('to')}"
    return str(payload)[:200]
