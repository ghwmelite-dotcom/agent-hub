"""DashboardBroker — in-process pub/sub for live dashboard updates.

Publishers (repos, db) call `publish(event)` after commits. Subscribers
(HTTP SSE handlers) iterate `subscribe()`. Each subscriber has its own
asyncio.Queue; a full queue drops the subscriber so a slow browser
can't back-pressure the orchestrator.

Snapshots are computed by reading SQLite. The broker is the only place
that knows about both event delivery AND the snapshot shape, which
keeps the HTTP layer dumb.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiosqlite
import structlog

from agent_hub.dashboard.events import Event

log = structlog.get_logger(__name__)


_QUEUE_MAXSIZE = 100


class DashboardBroker:
    """In-process pub/sub for dashboard events + snapshot reader."""

    def __init__(self, db_path: Path | None, queue_maxsize: int = _QUEUE_MAXSIZE):
        self.db_path = db_path
        self._queue_maxsize = queue_maxsize
        self._subscribers: set[asyncio.Queue[Event]] = set()

    # ------------------------------------------------------------------
    # Pub/sub
    # ------------------------------------------------------------------

    async def subscribe(self) -> AsyncIterator[Event]:
        """Yield events as they're published.

        Each subscriber gets its own bounded queue. If the queue fills
        (slow consumer), the subscriber is dropped — the browser will
        reconnect and re-snapshot.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._subscribers.add(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            self._subscribers.discard(queue)

    async def publish(self, event: Event) -> None:
        """Fan event out to every subscriber. Drops slow subscribers."""
        dropped: list[asyncio.Queue[Event]] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dropped.append(q)
                log.warning("dashboard.subscriber_dropped_queue_full")
        for q in dropped:
            self._subscribers.discard(q)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    async def snapshot(self) -> dict[str, Any]:
        """Build a complete state snapshot for the active workspace.

        Read by `GET /api/state` on connect AND on reconnect. The browser
        diffs against this rather than trying to replay missed events.
        """
        if self.db_path is None:
            return _empty_snapshot()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            workspace = await _read_active_workspace(conn)
            active_tasks = await _read_active_tasks(conn)
            pending_gates = await _read_pending_gates(conn)
            recent_events = await _read_recent_events(conn)
            stats = await _read_stats(conn)
        return {
            "workspace": workspace,
            "stats": stats,
            "active_tasks": active_tasks,
            "pending_gates": pending_gates,
            "recent_events": recent_events,
        }


def _empty_snapshot() -> dict[str, Any]:
    return {
        "workspace": None,
        "stats": {"running": 0, "pending": 0, "done_24h": 0, "queue": 0},
        "active_tasks": [],
        "pending_gates": [],
        "recent_events": [],
    }


async def _read_active_workspace(conn: aiosqlite.Connection) -> str | None:
    cur = await conn.execute(
        "SELECT value FROM settings_kv WHERE key = 'active_workspace'"
    )
    row = await cur.fetchone()
    return row["value"] if row else None


async def _read_active_tasks(conn: aiosqlite.Connection) -> list[dict]:
    cur = await conn.execute(
        "SELECT id, title, status, owner, created_at, updated_at, "
        "       cost_usd_total, worktree_path, branch_name "
        "FROM tasks "
        "WHERE status NOT IN ('done', 'blocked', 'cancelled') "
        "ORDER BY id DESC LIMIT 50"
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _read_pending_gates(conn: aiosqlite.Connection) -> list[dict]:
    cur = await conn.execute(
        "SELECT g.id, g.task_id, g.kind, g.requested_at, t.title "
        "FROM gates g JOIN tasks t ON t.id = g.task_id "
        "WHERE g.resolved_at IS NULL "
        "ORDER BY g.requested_at ASC"
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _read_recent_events(conn: aiosqlite.Connection) -> list[dict]:
    cur = await conn.execute(
        "SELECT id, task_id, ts, actor, kind, payload_json "
        "FROM task_events ORDER BY id DESC LIMIT 50"
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _read_stats(conn: aiosqlite.Connection) -> dict[str, int]:
    cur = await conn.execute(
        "SELECT "
        "  SUM(CASE WHEN status NOT IN ('done','blocked','cancelled') THEN 1 ELSE 0 END) AS running,"
        "  SUM(CASE WHEN status = 'design_review' THEN 1 ELSE 0 END) AS pending,"
        "  SUM(CASE WHEN status = 'done' AND updated_at > datetime('now','-1 day') THEN 1 ELSE 0 END) AS done_24h "
        "FROM tasks"
    )
    row = await cur.fetchone()
    cur2 = await conn.execute(
        "SELECT COUNT(*) AS q FROM handoff_queue WHERE claimed_at IS NULL"
    )
    q_row = await cur2.fetchone()
    return {
        "running": int(row["running"] or 0),
        "pending": int(row["pending"] or 0),
        "done_24h": int(row["done_24h"] or 0),
        "queue": int(q_row["q"] or 0),
    }


# ----------------------------------------------------------------------
# Module-level singleton helpers
# ----------------------------------------------------------------------

_BROKER: DashboardBroker | None = None


def get_broker() -> DashboardBroker | None:
    """Return the process-wide broker, or None if unset (e.g. in tests)."""
    return _BROKER


def set_broker(broker: DashboardBroker | None) -> None:
    """Install or clear the process-wide broker. Called from __main__."""
    global _BROKER
    _BROKER = broker


async def publish_if_set(event: Event) -> None:
    """Publish to the singleton broker if one is installed.

    Repos call this after commits. When no broker is set (tests, or
    dashboard disabled via DASHBOARD_PORT=0), this is a no-op.
    """
    broker = get_broker()
    if broker is None:
        return
    try:
        await broker.publish(event)
    except Exception:  # noqa: BLE001
        log.exception("dashboard.publish_failed")
