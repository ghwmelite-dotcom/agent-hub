"""Epic auto-completion: when the last leaf of an epic transitions to
done, mark the epic done.

Called from the orchestrator after observing a status_change event
that lands a leaf in done."""

from __future__ import annotations

from pathlib import Path

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.repository import TaskRepository


async def maybe_complete_epic(*, task_id: int, db_path: Path) -> int | None:
    """If task_id has a parent and all its siblings are done, mark the
    parent done. Returns the parent's id if it was transitioned, else None.
    """
    repo = TaskRepository(db_path)
    task = await repo.get(task_id)
    if task is None or task.parent_id is None:
        return None

    parent = await repo.get(task.parent_id)
    if parent is None or parent.status == TaskStatus.DONE:
        return None

    siblings = await repo.list(parent_id=parent.id)
    if not siblings:
        return None
    if not all(s.status == TaskStatus.DONE for s in siblings):
        return None

    # Walk the parent through valid transitions to DONE.
    # Valid path: PENDING -> PLANNING -> IN_PROGRESS -> REVIEW -> DONE
    try:
        current_status = parent.status
        if current_status == TaskStatus.PENDING:
            await repo.update(parent.id, status=TaskStatus.PLANNING)
            current_status = TaskStatus.PLANNING
        if current_status == TaskStatus.PLANNING:
            await repo.update(parent.id, status=TaskStatus.IN_PROGRESS)
            current_status = TaskStatus.IN_PROGRESS
        if current_status == TaskStatus.IN_PROGRESS:
            await repo.update(parent.id, status=TaskStatus.REVIEW)
            current_status = TaskStatus.REVIEW
        if current_status == TaskStatus.REVIEW:
            await repo.update(parent.id, status=TaskStatus.DONE)
        else:
            # Status is something we can't transition to DONE from (e.g. BLOCKED)
            return None
    except InvalidTransition:
        return None

    return parent.id
