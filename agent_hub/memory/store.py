"""Persistent project-scoped memory.

Keyed by workspace path; shared across all agents working on that workspace.
Auto-captured at orchestrator hook points (see memory/capture.py) and
injected into agent system prompts at task start (see agents/runner_options.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


_VALID_TYPES = {"project_fact", "lesson", "preference", "decision"}


# Per-role memory-type filtering. Roles not listed default to all types.
_ROLE_TYPE_ALLOWLIST: dict[str, set[str]] = {
    "pm": {"project_fact", "preference", "lesson", "decision"},
    "architect": {"project_fact", "preference", "lesson", "decision"},
    "quant": {"project_fact", "preference", "lesson", "decision"},
    "reviewer": {"project_fact", "preference", "lesson", "decision"},
    "fullstack-engineer": {"project_fact", "preference", "lesson"},
    "implementer": {"project_fact", "preference", "lesson"},
    "qa": {"project_fact", "lesson"},
    "backtest-analyst": {"project_fact", "lesson"},
    "researcher": {"project_fact", "preference"},
    "senior-uiux-designer": {"project_fact", "preference"},
}

_TYPE_HEADINGS = {
    "project_fact": "### Conventions",
    "preference":   "### Preferences (from user)",
    "lesson":       "### Recent lessons",
    "decision":     "### Recent decisions",
}

# Render order — controls the order sections appear in the assembled section.
_TYPE_ORDER = ("project_fact", "preference", "lesson", "decision")

# Soft caps per type (how many entries to consider before size capping).
_TYPE_LIMITS = {
    "project_fact": 10,
    "preference":   100,  # all non-archived; cap is defensive
    "lesson":       5,
    "decision":     5,
}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """Read/write the project_memory table.

    Async, per-call connect (matches the rest of the codebase).
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> Any:
        return aiosqlite.connect(self.db_path)

    async def insert(
        self,
        *,
        workspace: str,
        type: str,
        agent_source: str | None,
        title: str,
        body: str,
        related_task: int | None = None,
    ) -> int:
        """Insert a memory row, deduping on (workspace, type, title).

        On exact-title match (non-archived), no new row is inserted —
        instead `use_count` is bumped on the existing row and its id
        is returned. Body of the existing row is preserved.
        """
        if type not in _VALID_TYPES:
            raise ValueError(f"invalid memory type: {type!r}")
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT id FROM project_memory "
                "WHERE workspace = ? AND type = ? AND title = ? AND archived = 0",
                (workspace, type, title),
            )
            existing = await cur.fetchone()
            if existing is not None:
                await conn.execute(
                    "UPDATE project_memory SET use_count = use_count + 1 "
                    "WHERE id = ?",
                    (existing["id"],),
                )
                await conn.commit()
                return int(existing["id"])

            cur = await conn.execute(
                "INSERT INTO project_memory "
                "(workspace, type, agent_source, title, body, related_task, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (workspace, type, agent_source, title, body, related_task, _utcnow_iso()),
            )
            await conn.commit()
            return int(cur.lastrowid)

    async def list(
        self,
        *,
        workspace: str,
        type: str | None = None,
        include_archived: bool = False,
    ) -> list[dict]:
        """List entries for a workspace, newest first."""
        clauses = ["workspace = ?"]
        params: list[Any] = [workspace]
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if not include_archived:
            clauses.append("archived = 0")
        where = " AND ".join(clauses)
        async with self._connect() as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                f"SELECT * FROM project_memory WHERE {where} "
                f"ORDER BY id DESC",
                params,
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def archive(self, entry_id: int) -> None:
        """Soft delete — sets archived = 1."""
        async with self._connect() as conn:
            await conn.execute(
                "UPDATE project_memory SET archived = 1 WHERE id = ?",
                (entry_id,),
            )
            await conn.commit()

    async def load_for_prompt(
        self,
        *,
        workspace: str,
        agent_name: str,
    ) -> str:
        """Build the `## Project memory` system-prompt section.

        Returns the assembled markdown string, or "" if nothing applies.
        Bumps `use_count` and `last_used_at` on every entry included.
        """
        allowed = _ROLE_TYPE_ALLOWLIST.get(agent_name, set(_VALID_TYPES))
        async with self._connect() as conn:
            conn.row_factory = aiosqlite.Row

            entries_by_type: dict[str, list[dict]] = {}
            for t in _TYPE_ORDER:
                if t not in allowed:
                    continue
                limit = _TYPE_LIMITS[t]
                # project_fact orders by use_count DESC, recency tiebreak;
                # everything else by recency.
                if t == "project_fact":
                    order = "use_count DESC, id DESC"
                else:
                    order = "id DESC"
                cur = await conn.execute(
                    f"SELECT * FROM project_memory "
                    f"WHERE workspace = ? AND type = ? AND archived = 0 "
                    f"ORDER BY {order} LIMIT ?",
                    (workspace, t, limit),
                )
                entries_by_type[t] = [dict(r) for r in await cur.fetchall()]

            if not any(entries_by_type.values()):
                return ""

            # Render
            lines = [f"## Project memory — {workspace}", ""]
            for t in _TYPE_ORDER:
                rows = entries_by_type.get(t, [])
                if not rows:
                    continue
                lines.append(_TYPE_HEADINGS[t])
                for row in rows:
                    suffix = (
                        f"  (used {row['use_count']}×)"
                        if t == "project_fact" and row["use_count"] > 0
                        else ""
                    )
                    lines.append(f"- {row['title']}{suffix}")
                lines.append("")
            section = "\n".join(lines).rstrip() + "\n"

            # Bookkeeping: bump use_count and last_used_at for every included id.
            included_ids = [
                row["id"]
                for rows in entries_by_type.values()
                for row in rows
            ]
            if included_ids:
                placeholders = ",".join("?" for _ in included_ids)
                await conn.execute(
                    f"UPDATE project_memory "
                    f"SET use_count = use_count + 1, last_used_at = ? "
                    f"WHERE id IN ({placeholders})",
                    [_utcnow_iso(), *included_ids],
                )
                await conn.commit()

            return section
