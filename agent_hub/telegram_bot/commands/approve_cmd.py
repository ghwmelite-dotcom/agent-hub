"""Pure handler for /approve <id> — resolves the pending design gate
and advances the task from design_review to ready.

Kept pure (no PTB import) so it can be unit-tested without a bot.
The Telegram glue (extracting task_id from the message, sending the
reply) lives in agent_hub/telegram_bot/bot.py.
"""

from __future__ import annotations

from pathlib import Path

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.repository import TaskRepository


async def handle_approve(*, task_id: int, db_path: Path) -> str:
    """Resolve the design gate (if any) and flip the task to ready.

    Returns a human-readable reply suitable for posting back to the
    user's chat.
    """
    repo = TaskRepository(db_path)
    gates = GateRepository(db_path)

    task = await repo.get(task_id)
    if task is None:
        return f"Task #{task_id} not found."

    status = await gates.status(task_id=task_id, kind="design")
    if status == "none":
        return f"Task #{task_id} has no pending design gate to approve."
    if status != "pending":
        return f"Task #{task_id} gate is already {status}."

    await gates.resolve(task_id=task_id, kind="design", resolution="approved")
    try:
        await repo.update(task_id, status=TaskStatus.READY)
    except InvalidTransition as exc:
        return f"Approved the gate but couldn't advance status: {exc}"

    return f"✅ Task #{task_id} approved — moving to ready."
