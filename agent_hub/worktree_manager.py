"""Worktree manager — wraps `git worktree` subprocesses and tracks
state via WorktreeRepository.

Branch naming convention: `task/<id>-<slug>` where slug is the task
title normalised to lowercase ASCII alphanumerics and hyphens, max
60 chars. Empty slugs (unicode-only or empty titles) fall back to
just `task/<id>`.
"""

from __future__ import annotations

import re

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
