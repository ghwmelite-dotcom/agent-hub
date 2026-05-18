"""Push the task's branch to origin after the task transitions to done.

Returns a dict with `pushed: bool`, `branch: str | None`, and an
`error` key on failure. The orchestrator's handoff loop calls this
when it observes a status transition to done.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository


@dataclass
class RemoteCheck:
    ok: bool
    url: str | None = None
    error: str | None = None


async def verify_remote_configured(repo_root: Path) -> RemoteCheck:
    """Confirm `origin` is set on repo_root before agents burn tokens.

    Run before `/approve` creates the worktree — if the user never did
    `git remote add origin <url>`, the whole task pipeline will end with
    a push failure. Catching it here saves an entire agent loop.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "remote", "get-url", "origin",
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode != 0:
        err = stderr_b.decode("utf-8", errors="replace").strip()
        return RemoteCheck(
            ok=False,
            error=err or "git remote get-url origin failed",
        )
    return RemoteCheck(ok=True, url=stdout_b.decode("utf-8", errors="replace").strip())


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
