"""Worktree manager — wraps `git worktree` subprocesses and tracks
state via WorktreeRepository.

Branch naming convention: `task/<id>-<slug>` where slug is the task
title normalised to lowercase ASCII alphanumerics and hyphens, max
60 chars. Empty slugs (unicode-only or empty titles) fall back to
just `task/<id>`.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from agent_hub.tasks.worktree_repo import WorktreeRepository

_SLUG_REPLACE_RE = re.compile(r"[^a-z0-9]+")
_TITLE_MAX = 60


def branch_slug(task_id: int, title: str) -> str:
    """Return a git-safe branch name for the given task.

    Format: task/<id>[-<slug>] where slug is at most 60 chars.
    """
    lowered = title.lower()
    slugged = _SLUG_REPLACE_RE.sub("-", lowered).strip("-")
    if not slugged:
        return f"task/{task_id}"
    truncated = slugged[:_TITLE_MAX].rstrip("-")
    if not truncated:
        return f"task/{task_id}"
    return f"task/{task_id}-{truncated}"


class WorktreeManager:
    def __init__(self, repo_root: Path, worktrees_root: Path, db_path: Path):
        self.repo_root = Path(repo_root)
        self.worktrees_root = Path(worktrees_root)
        self.db_path = Path(db_path)
        self._repo = WorktreeRepository(self.db_path)

    async def _run_git(self, *args: str) -> tuple[int, str, str]:
        """Run `git <args>` from repo_root. Returns (returncode, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(self.repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        return proc.returncode or 0, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")

    async def create(self, *, task_id: int, title: str, base_branch: str = "main") -> dict:
        """Create a worktree at <worktrees_root>/<task_id>/ on branch task/<id>-<slug>.

        Returns {"path": str, "branch": str}. Records in the worktrees table.
        """
        branch = branch_slug(task_id, title)
        path = self.worktrees_root / str(task_id)
        self.worktrees_root.mkdir(parents=True, exist_ok=True)

        rc, stdout, stderr = await self._run_git(
            "worktree", "add", "-b", branch, str(path), base_branch,
        )
        if rc != 0:
            raise RuntimeError(
                f"git worktree add failed (rc={rc}): {stderr.strip() or stdout.strip()}"
            )

        await self._repo.record(
            task_id=task_id, path=str(path), branch=branch, base_branch=base_branch,
        )
        return {"path": str(path), "branch": branch}

    async def path(self, task_id: int) -> str | None:
        """Return the recorded worktree path for task_id, or None."""
        row = await self._repo.get_by_task(task_id)
        return row.path if row else None

    async def cleanup(self, task_id: int) -> None:
        """Remove the worktree from disk and mark cleaned_at.

        Refuses to remove a dirty worktree (uncommitted changes) — the
        agent's work is left in place for human inspection. The DB row
        is NOT marked cleaned in that case so the orchestrator can flag
        the task as blocked.
        """
        row = await self._repo.get_by_task(task_id)
        if row is None or row.cleaned_at is not None:
            return  # nothing to do

        # Check for uncommitted changes inside the worktree.
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            cwd=row.path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await proc.communicate()
        if stdout_b.strip():
            raise RuntimeError(
                f"Worktree {row.path} has uncommitted changes; refusing to remove. "
                f"Resolve manually or commit first."
            )

        rc, stdout, stderr = await self._run_git("worktree", "remove", row.path)
        if rc != 0:
            raise RuntimeError(
                f"git worktree remove failed (rc={rc}): {stderr.strip() or stdout.strip()}"
            )
        await self._repo.mark_cleaned(task_id)
