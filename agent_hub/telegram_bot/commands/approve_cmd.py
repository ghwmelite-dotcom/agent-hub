"""Pure handler for /approve <id> — resolves the pending design gate
and advances the task from design_review to ready.

When repo_root + worktrees_root are provided (production path), also
creates the per-task git worktree and enqueues the follow-on handoff
to fullstack-engineer. When omitted (test path), stops at status=ready.

Kept pure (no PTB import) so it can be unit-tested without a bot.
The Telegram glue (extracting task_id from the message, sending the
reply) lives in agent_hub/telegram_bot/bot.py.
"""

from __future__ import annotations

from pathlib import Path

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


async def handle_approve(
    *,
    task_id: int,
    db_path: Path,
    repo_root: Path | None = None,
    worktrees_root: Path | None = None,
) -> str:
    """Resolve the design gate (if any) and flip the task to ready.

    Production path (both repo_root and worktrees_root provided):
    - Resolves gate as approved
    - Flips status from design_review → ready
    - Creates a git worktree at <worktrees_root>/<task_id>/ on branch task/<id>-<slug>
    - Enqueues handoff to fullstack-engineer with task context

    Test/minimal path (repo_root or worktrees_root missing):
    - Resolves gate as approved
    - Flips status from design_review → ready
    - Returns
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

    # Preflight: if we're on the production path, confirm `origin` is
    # configured BEFORE we mutate any state. Otherwise the task pipeline
    # ends with a push failure after agents have already burned tokens.
    if repo_root is not None:
        from agent_hub.orchestrator.push import verify_remote_configured
        check = await verify_remote_configured(repo_root)
        if not check.ok:
            return (
                f"❌ Task #{task_id} can't ship — `origin` is not configured "
                f"on {repo_root}. Run `git remote add origin <url>` there, "
                f"then `/approve {task_id}` again.\n"
                f"({check.error})"
            )

    await gates.resolve(task_id=task_id, kind="design", resolution="approved")
    try:
        await repo.update(task_id, status=TaskStatus.READY)
    except InvalidTransition as exc:
        return f"Approved the gate but couldn't advance status: {exc}"

    # If we don't have the workspace info, stop here. Tests use this path.
    if repo_root is None or worktrees_root is None:
        return f"✅ Task #{task_id} approved — moving to ready."

    # Production path: create worktree + handoff to fullstack-engineer.
    from agent_hub.worktree_manager import WorktreeManager
    manager = WorktreeManager(
        repo_root=repo_root,
        worktrees_root=worktrees_root,
        db_path=db_path,
    )
    try:
        wt = await manager.create(task_id=task_id, title=task.title, base_branch="main")
    except RuntimeError as exc:
        return (
            f"✅ Task #{task_id} approved, but worktree creation failed: {exc}\n"
            f"Status is at ready — investigate manually."
        )

    queue = HandoffQueue(db_path)
    await queue.enqueue(
        task_id=task_id,
        from_agent="user",
        to_agent="fullstack-engineer",
        message=(
            f"Design approved by user. Implement per the architect's design comment on the task. "
            f"Your worktree is at {wt['path']} on branch {wt['branch']}. "
            f"Start by calling tasks.get({task_id}) to read the design."
        ),
    )
    return (
        f"✅ Task #{task_id} approved — fullstack-engineer is on it.\n"
        f"Branch: `{wt['branch']}`"
    )
