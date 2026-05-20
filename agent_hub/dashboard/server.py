"""DashboardServer — aiohttp app serving the live monitor at 127.0.0.1:8765.

Four routes:
- GET /            → single-page HTML
- GET /api/state   → snapshot JSON
- GET /api/events  → SSE stream of broker events
- GET /api/task/<id> → full event timeline for one task

Workspace filtering is applied to /api/state and /api/events so the
browser only sees activity for the currently active workspace.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite
import structlog
from aiohttp import web

from agent_hub.dashboard.broker import DashboardBroker
from agent_hub.dashboard.events import (
    Event,
    TaskChanged,
    TaskEvent,
    GateChanged,
    WorkspaceChanged,
    to_json,
)

log = structlog.get_logger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_INDEX_PATH = _STATIC_DIR / "index.html"
_SSE_PING_INTERVAL = 15.0


class DashboardServer:
    """aiohttp server binding to 127.0.0.1 only."""

    def __init__(
        self,
        broker: DashboardBroker,
        db_path: Path,
        port: int = 8765,
    ):
        self.broker = broker
        self.db_path = db_path
        self.port = port
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._index_html: str = ""

    async def start(self) -> None:
        # Read index.html once at startup (cached for every request).
        if _INDEX_PATH.exists():
            self._index_html = _INDEX_PATH.read_text(encoding="utf-8")
        else:
            self._index_html = "<html><body>Dashboard frontend missing.</body></html>"

        app = web.Application()
        app.add_routes([
            web.get("/", self._handle_index),
            web.get("/api/state", self._handle_state),
            web.get("/api/events", self._handle_events),
            web.get("/api/task/{task_id}", self._handle_task),
        ])

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner, host="127.0.0.1", port=self.port,
        )
        try:
            await self._site.start()
            log.info("dashboard.started", port=self.port)
        except OSError as exc:
            log.warning("dashboard.port_in_use", port=self.port, error=str(exc))
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(
            text=self._index_html,
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    async def _handle_state(self, request: web.Request) -> web.Response:
        snapshot = await self.broker.snapshot()
        return web.json_response(snapshot, headers={"Cache-Control": "no-store"})

    async def _handle_task(self, request: web.Request) -> web.Response:
        task_id = int(request.match_info["task_id"])
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            tcur = await conn.execute(
                "SELECT id, title, description, status, owner, "
                "       worktree_path, branch_name, created_at, "
                "       updated_at, cost_usd_total "
                "FROM tasks WHERE id = ?",
                (task_id,),
            )
            task_row = await tcur.fetchone()
            if task_row is None:
                return web.json_response({"error": "not found"}, status=404)

            ecur = await conn.execute(
                "SELECT id, ts, actor, kind, payload_json "
                "FROM task_events WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            )
            event_rows = await ecur.fetchall()

        return web.json_response({
            "task": dict(task_row),
            "events": [dict(r) for r in event_rows],
        })

    async def _handle_events(self, request: web.Request) -> web.StreamResponse:
        # Resolve the active workspace ONCE at connect time; refreshed via
        # workspace_changed events the client will see.
        active_workspace = await _read_active_workspace(self.db_path)

        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)

        ping_task = asyncio.create_task(self._ping_loop(resp))
        try:
            async for event in self.broker.subscribe():
                if not _event_matches_workspace(event, active_workspace):
                    continue
                payload = to_json(event)
                try:
                    await resp.write(f"data: {payload}\n\n".encode("utf-8"))
                except (ConnectionResetError, asyncio.CancelledError):
                    break
                # Refresh active_workspace on workspace_changed so subsequent
                # events are filtered against the new value.
                if isinstance(event, WorkspaceChanged):
                    active_workspace = event.workspace
        finally:
            ping_task.cancel()
        return resp

    async def _ping_loop(self, resp: web.StreamResponse) -> None:
        """Send a comment-line ping every 15s so proxies/tabs don't kill the stream."""
        try:
            while True:
                await asyncio.sleep(_SSE_PING_INTERVAL)
                try:
                    await resp.write(b": ping\n\n")
                except (ConnectionResetError, asyncio.CancelledError):
                    return
        except asyncio.CancelledError:
            return


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _event_matches_workspace(event: Event, active_workspace: str | None) -> bool:
    """Filter SSE events so only the active workspace's activity flows.

    WorkspaceChanged events always pass — the client uses them to learn
    that it should re-fetch /api/state.
    """
    if isinstance(event, WorkspaceChanged):
        return True
    if active_workspace is None:
        return True  # no filter set — pass through everything
    return getattr(event, "workspace", None) == active_workspace


async def _read_active_workspace(db_path: Path) -> str | None:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT value FROM settings_kv WHERE key = 'active_workspace'"
        )
        row = await cur.fetchone()
    return row["value"] if row else None
