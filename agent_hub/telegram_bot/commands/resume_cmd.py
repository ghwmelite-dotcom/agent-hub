"""Pure handler for /resume <id>.

Two cases:
- BLOCKED → flip to planning and hand off to PM with the block context.
- Any other paused state (in_progress, review, planning) → re-dispatch
  to the current owner (or PM if owner is unset).
"""

from __future__ import annotations

from pathlib import Path

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


async def handle_resume(*, task_id: int, db_path: Path) -> str:
    repo = TaskRepository(db_path)
    queue = HandoffQueue(db_path)

    task = await repo.get(task_id)
    if task is None:
        return f"Task #{task_id} not found."

    if task.status == TaskStatus.BLOCKED:
        try:
            await repo.update(task_id, status=TaskStatus.PLANNING)
        except InvalidTransition as exc:
            return f"Couldn't resume from blocked: {exc}"
        await queue.enqueue(
            task_id=task_id,
            from_agent="user",
            to_agent="pm",
            message=f"User requested resume of blocked task #{task_id}. Reassess and decide the next step.",
        )
        return f"▶️ Task #{task_id} resumed — PM is taking another look."

    # Non-blocked: re-dispatch to current owner.
    to_agent = task.owner or "pm"
    await queue.enqueue(
        task_id=task_id,
        from_agent="user",
        to_agent=to_agent,
        message=f"User requested resume of task #{task_id}. Continue where you left off.",
    )
    return f"▶️ Task #{task_id} resumed — sent to @{to_agent}."
