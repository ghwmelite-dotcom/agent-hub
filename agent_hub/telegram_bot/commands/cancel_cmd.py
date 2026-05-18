"""Pure handler for /cancel <id>.

Abort a running task: flip to BLOCKED, drop pending handoff rows,
optionally reset the agent SDK session for (agent, task_id) so the
agent's next turn (if any) starts fresh.

Done tasks are not cancellable — they've already shipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


async def handle_cancel(
    *,
    task_id: int,
    db_path: Path,
    runner: Any | None = None,
) -> str:
    """Cancel a non-terminal task.

    `runner` is an AgentRunner-shaped object with a `reset(agent_name, task_id=...)`
    coroutine. Passing it lets us drop the live SDK session for whichever
    agent currently owns the task so a future /resume starts clean. Tests
    can pass None to skip the session reset.
    """
    repo = TaskRepository(db_path)
    queue = HandoffQueue(db_path)

    task = await repo.get(task_id)
    if task is None:
        return f"Task #{task_id} not found."

    if task.status == TaskStatus.DONE:
        return f"Task #{task_id} is already done — nothing to cancel."
    if task.status == TaskStatus.BLOCKED:
        return f"Task #{task_id} is already blocked."

    try:
        await repo.update(task_id, status=TaskStatus.BLOCKED)
    except InvalidTransition as exc:
        return f"Couldn't cancel task #{task_id}: {exc}"

    dropped = await queue.drop_pending_for_task(task_id)
    await repo.comment(task_id, actor="user", body="Cancelled by user.")

    if runner is not None and task.owner:
        try:
            await runner.reset(task.owner, task_id=task_id)
        except KeyError:
            # Owner is set but not a known role — leave the session dict alone.
            pass

    summary = f"🛑 Task #{task_id} cancelled."
    if dropped:
        summary += f" Dropped {dropped} pending handoff{'s' if dropped != 1 else ''}."
    return summary
