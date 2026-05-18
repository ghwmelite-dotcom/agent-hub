"""Push the task's branch to origin after the task transitions to done.

Returns a dict with `pushed: bool`, `branch: str | None`, and an
`error` key on failure. The orchestrator's handoff loop calls this
when it observes a status transition to done.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository


async def push_task_branch(*, task_id: int, repo_root: Path, db_path: Path) -> dict:
    repo = TaskRepository(db_path)
    wt_repo = WorktreeRepository(db_path)

    task = await repo.get(task_id)
    if task is None:
        return {"pushed": False, "branch": None, "error": f"Unknown task #{task_id}"}

    row = await wt_repo.get_by_task(task_id)
    if row is None:
        return {"pushed": False, "branch": None, "error": f"No worktree recorded for #{task_id}"}

    proc = await asyncio.create_subprocess_exec(
        "git", "push", "origin", row.branch,
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode != 0:
        return {
            "pushed": False,
            "branch": row.branch,
            "error": stderr_b.decode("utf-8", errors="replace").strip() or
                     stdout_b.decode("utf-8", errors="replace").strip(),
        }
    return {"pushed": True, "branch": row.branch}
