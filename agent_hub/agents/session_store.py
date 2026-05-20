"""Persistent (agent, task_id) → SDK session_id mapping.

Used by AgentRunner to keep the Claude Agent SDK pointed at the SAME
session across process restarts. The Claude Code CLI persists the
session's conversation history to a JSONL file under its config dir,
so passing the same UUID on reconnect picks up where we left off
(re-uses the system prompt, prior tool calls, agent's mental state).

Without this, a restart spawns a brand-new conversation for every
(agent, task_id) pair — the agent has to re-derive its understanding
of the task from MCP state. Functionally fine but wasteful.

`task_id` is stored as integer 0 when the runner is in "no-task" mode
(general @-mention conversation, not tied to a task). Real task IDs
start at 1.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite


_NO_TASK_SENTINEL = 0


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key_task_id(task_id: int | None) -> int:
    """Map None → 0 sentinel for the (agent, task_id) primary key.

    Real task IDs start at 1 (SQLite AUTOINCREMENT), so 0 is unambiguous.
    """
    return _NO_TASK_SENTINEL if task_id is None else int(task_id)


class AgentSessionStore:
    """Read/write the agent_sessions table.

    Thin async wrapper. Caller is responsible for connection lifetime
    matching the rest of the codebase's per-call connect pattern.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> Any:
        return aiosqlite.connect(self.db_path)

    async def get_or_create(
        self,
        *,
        agent_name: str,
        task_id: int | None,
    ) -> str:
        """Return the persisted session_id for (agent, task_id), or
        create one if absent.

        UUIDs are generated with uuid4 — random enough that collisions
        across runs are non-issues.
        """
        key_id = _key_task_id(task_id)
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT session_id FROM agent_sessions "
                "WHERE agent_name = ? AND task_id = ?",
                (agent_name, key_id),
            )
            row = await cur.fetchone()
            if row is not None:
                return str(row["session_id"])

            new_session = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO agent_sessions "
                "(agent_name, task_id, session_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (agent_name, key_id, new_session, _utcnow_iso()),
            )
            await conn.commit()
            return new_session

    async def get(
        self,
        *,
        agent_name: str,
        task_id: int | None,
    ) -> str | None:
        """Lookup without insert. Returns None when no session is recorded."""
        key_id = _key_task_id(task_id)
        async with self._connect() as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT session_id FROM agent_sessions "
                "WHERE agent_name = ? AND task_id = ?",
                (agent_name, key_id),
            )
            row = await cur.fetchone()
        return str(row["session_id"]) if row else None

    async def forget(
        self,
        *,
        agent_name: str,
        task_id: int | None,
    ) -> None:
        """Drop the (agent, task_id) row. Idempotent.

        Used when a session goes bad — caller wants the NEXT connect to
        create a fresh UUID rather than try to resume a broken session.
        """
        key_id = _key_task_id(task_id)
        async with self._connect() as conn:
            await conn.execute(
                "DELETE FROM agent_sessions "
                "WHERE agent_name = ? AND task_id = ?",
                (agent_name, key_id),
            )
            await conn.commit()

    async def get_fingerprint(
        self,
        *,
        agent_name: str,
        task_id: int | None,
    ) -> str | None:
        """Read the last-known memory fingerprint for (agent, task).

        Returns None if no session row exists OR fingerprint is unset.
        """
        key_id = _key_task_id(task_id)
        async with self._connect() as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT memory_fingerprint FROM agent_sessions "
                "WHERE agent_name = ? AND task_id = ?",
                (agent_name, key_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        fp = row["memory_fingerprint"]
        return str(fp) if fp is not None else None

    async def set_fingerprint(
        self,
        *,
        agent_name: str,
        task_id: int | None,
        fingerprint: str,
    ) -> None:
        """Persist the current memory fingerprint for (agent, task).

        Upserts: if no session row exists yet, create one with a fresh
        UUID so the column has somewhere to live. The UUID won't be
        used by the SDK until get_or_create is called normally.
        """
        key_id = _key_task_id(task_id)
        async with self._connect() as conn:
            cur = await conn.execute(
                "UPDATE agent_sessions SET memory_fingerprint = ? "
                "WHERE agent_name = ? AND task_id = ?",
                (fingerprint, agent_name, key_id),
            )
            if cur.rowcount == 0:
                # No session row yet — create a placeholder so the
                # fingerprint has somewhere to live. The session_id is
                # a fresh UUID; if get_or_create later runs it will see
                # this row and return this UUID.
                await conn.execute(
                    "INSERT INTO agent_sessions "
                    "(agent_name, task_id, session_id, created_at, memory_fingerprint) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (agent_name, key_id, str(uuid.uuid4()),
                     _utcnow_iso(), fingerprint),
                )
            await conn.commit()
