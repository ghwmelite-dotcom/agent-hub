"""Lightweight SQLite store for conversation history, tasks, and approvals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import structlog

log = structlog.get_logger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    agent       TEXT    NOT NULL,
    direction   TEXT    NOT NULL CHECK (direction IN ('in', 'out')),
    content     TEXT    NOT NULL,
    metadata    TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_agent_ts ON messages (agent, ts);

CREATE TABLE IF NOT EXISTS settings_kv (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    agent       TEXT    NOT NULL,
    summary     TEXT    NOT NULL,
    payload     TEXT,
    status      TEXT    NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    decided_at  TEXT
);
"""


class Database:
    """Thin async wrapper around an SQLite file."""

    def __init__(self, path: Path):
        self.path = path

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as conn:
            await conn.executescript(SCHEMA)
            await conn.commit()
        log.info("db.ready", path=str(self.path))

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def log_message(
        self,
        *,
        agent: str,
        direction: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "INSERT INTO messages (ts, agent, direction, content, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    _now_iso(),
                    agent,
                    direction,
                    content,
                    json.dumps(metadata) if metadata else None,
                ),
            )
            await conn.commit()

    async def recent_messages(self, agent: str, limit: int = 20) -> list[dict]:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT ts, direction, content FROM messages "
                "WHERE agent = ? ORDER BY id DESC LIMIT ?",
                (agent, limit),
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows][::-1]

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    async def set_kv(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "INSERT INTO settings_kv (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, _now_iso()),
            )
            await conn.commit()

    async def get_kv(self, key: str) -> str | None:
        async with aiosqlite.connect(self.path) as conn:
            cursor = await conn.execute(
                "SELECT value FROM settings_kv WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------
    # Workspaces — remembered across restarts so the agents resume in
    # whatever project the user was last working on.
    # ------------------------------------------------------------------

    _ACTIVE_KEY = "active_workspace"
    _RECENT_KEY = "recent_workspaces"
    _MAX_RECENT = 10

    async def get_active_workspace(self) -> str | None:
        return await self.get_kv(self._ACTIVE_KEY)

    async def set_active_workspace(self, path: str) -> None:
        await self.set_kv(self._ACTIVE_KEY, path)
        await self._touch_recent(path)

    async def list_recent_workspaces(self) -> list[str]:
        raw = await self.get_kv(self._RECENT_KEY)
        if not raw:
            return []
        try:
            return list(json.loads(raw))
        except (ValueError, TypeError):
            return []

    async def _touch_recent(self, path: str) -> None:
        items = await self.list_recent_workspaces()
        # Move to front, dedupe, cap.
        items = [p for p in items if p != path]
        items.insert(0, path)
        items = items[: self._MAX_RECENT]
        await self.set_kv(self._RECENT_KEY, json.dumps(items))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
