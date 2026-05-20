"""Pure handler for /reject <id> <reason>.

Resolves the design gate as rejected, flips status back to planning,
and enqueues a handoff to architect with the user's feedback.
"""

from __future__ import annotations

from pathlib import Path

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


async def handle_reject(
    *, task_id: int, reason: str, db_path: Path, workspace: str | None = None
) -> str:
    reason = (reason or "").strip()
    if not reason:
        return "Reject requires a reason: /reject <id> <reason>"

    repo = TaskRepository(db_path)
    gates = GateRepository(db_path)
    queue = HandoffQueue(db_path)

    task = await repo.get(task_id)
    if task is None:
        return f"Task #{task_id} not found."

    status = await gates.status(task_id=task_id, kind="design")
    if status == "none":
        return f"Task #{task_id} has no pending design gate to reject."
    if status != "pending":
        return f"Task #{task_id} gate is already {status}."

    await gates.resolve(task_id=task_id, kind="design", resolution="rejected")
    await repo.comment(task_id, actor="user", body=f"Rejected: {reason}")

    from agent_hub.memory.capture import on_reject
    await on_reject(
        db_path=db_path,
        workspace=workspace,
        task_id=task_id,
        task_title=task.title,
        reason=reason,
    )

    try:
        await repo.update(task_id, status=TaskStatus.PLANNING)
    except InvalidTransition as exc:
        return f"Resolved as rejected but couldn't return to planning: {exc}"

    await queue.enqueue(
        task_id=task_id,
        from_agent="user",
        to_agent="architect",
        message=f"User rejected the design with feedback: {reason}",
    )
    return f"❌ Task #{task_id} rejected — returned to planning with feedback."
