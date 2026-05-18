"""Pure handler for /tasks — lists non-done tasks grouped by status."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.repository import TaskRepository


async def handle_tasks(*, db_path: Path) -> str:
    repo = TaskRepository(db_path)
    all_tasks = await repo.list()  # no filter

    active = [t for t in all_tasks if t.status != TaskStatus.DONE]
    if not active:
        return "No active tasks."

    by_status: dict[str, list] = defaultdict(list)
    for t in active:
        by_status[t.status.value].append(t)

    order = ["pending", "planning", "design_review", "ready",
             "in_progress", "review", "blocked"]
    lines: list[str] = []
    for status in order:
        if status not in by_status:
            continue
        lines.append(f"\n*{status}*")
        for t in by_status[status]:
            owner = f" → @{t.owner}" if t.owner else ""
            lines.append(f"  #{t.id} {t.title}{owner}")

    return "Active tasks:" + "\n".join(lines)
