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


_SCHEMA_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES tasks(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT,
    worktree_path TEXT,
    branch_name TEXT,
    origin_chat_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cost_usd_total REAL NOT NULL DEFAULT 0
);
"""


async def _migrate_tasks_cost_total(conn: aiosqlite.Connection) -> None:
    """Idempotent migration: ensure `tasks.cost_usd_total` exists.

    Pre-existing DBs predate the budget tracking, so add the column via
    ALTER TABLE if it isn't already there. Defaults to 0 so historical
    rows are valid.
    """
    cur = await conn.execute("PRAGMA table_info(tasks)")
    rows = await cur.fetchall()
    existing = {r[1] for r in rows}
    if "cost_usd_total" not in existing:
        await conn.execute(
            "ALTER TABLE tasks ADD COLUMN cost_usd_total REAL NOT NULL DEFAULT 0"
        )

_SCHEMA_TASK_EVENTS = """
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_events_task_id_ts ON task_events(task_id, ts);
"""

_SCHEMA_HANDOFF_QUEUE = """
CREATE TABLE IF NOT EXISTS handoff_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    message TEXT NOT NULL,
    enqueued_at TEXT NOT NULL,
    claimed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_handoff_queue_unclaimed ON handoff_queue(claimed_at) WHERE claimed_at IS NULL;
"""

_SCHEMA_GATES = """
CREATE TABLE IF NOT EXISTS gates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    kind TEXT NOT NULL,
    artifact_path TEXT,
    summary TEXT,
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT,
    notified_at TEXT,
    last_reminder_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_gates_pending ON gates(task_id, kind) WHERE resolved_at IS NULL;
"""


async def _migrate_gates_notified_at(conn: aiosqlite.Connection) -> None:
    """Idempotent migration: ensure `gates.notified_at` + `gates.last_reminder_at`
    exist.

    SQLite's CREATE TABLE IF NOT EXISTS does not add new columns to a
    pre-existing table. Existing deployments need an ALTER TABLE; we
    check first via PRAGMA so the migration is safe to re-run.
    """
    cur = await conn.execute("PRAGMA table_info(gates)")
    rows = await cur.fetchall()
    existing = {r[1] for r in rows}  # column name is index 1
    if "notified_at" not in existing:
        await conn.execute("ALTER TABLE gates ADD COLUMN notified_at TEXT")
    if "last_reminder_at" not in existing:
        await conn.execute("ALTER TABLE gates ADD COLUMN last_reminder_at TEXT")

_SCHEMA_WORKTREES = """
CREATE TABLE IF NOT EXISTS worktrees (
    task_id INTEGER PRIMARY KEY REFERENCES tasks(id),
    path TEXT NOT NULL,
    branch TEXT NOT NULL,
    base_branch TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cleaned_at TEXT
);
"""


class Database:
    """Thin async wrapper around an SQLite file."""

    def __init__(self, path: Path):
        self.path = path

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.executescript(SCHEMA)
            await conn.executescript(_SCHEMA_TASKS)
            await conn.executescript(_SCHEMA_TASK_EVENTS)
            await conn.executescript(_SCHEMA_HANDOFF_QUEUE)
            await conn.executescript(_SCHEMA_GATES)
            await conn.executescript(_SCHEMA_WORKTREES)
            await _migrate_gates_notified_at(conn)
            await _migrate_tasks_cost_total(conn)
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
