# Live Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a localhost-only live activity monitor at `http://localhost:8765` bundled into the bot process — read-only Mission Console aesthetic, SSE-driven updates, no separate deployment.

**Architecture:** New `agent_hub/dashboard/` package with three pieces: a `DashboardBroker` (in-process pub/sub + snapshot reader), a `DashboardServer` (aiohttp app on 127.0.0.1:8765 with 4 routes including SSE), and a single self-contained `static/index.html`. Existing repositories call `get_broker().publish(...)` after commits; the broker fans out to SSE subscribers. Repos remain test-friendly because `get_broker()` returns `None` when unset.

**Tech Stack:** Python 3.12, aiohttp 3.9+ (new dependency), aiosqlite (existing), pytest + pytest-asyncio (existing). Frontend is vanilla JS — no build step.

**Spec:** `docs/superpowers/specs/2026-05-20-dashboard-design.md`

---

## File Structure

**New files:**
- `agent_hub/dashboard/__init__.py` — package marker (empty)
- `agent_hub/dashboard/events.py` — Event dataclasses + JSON serializer
- `agent_hub/dashboard/broker.py` — DashboardBroker class + module-level get/set singleton
- `agent_hub/dashboard/server.py` — DashboardServer class with 4 aiohttp routes
- `agent_hub/dashboard/static/index.html` — single-page frontend
- `tests/test_dashboard_events.py`
- `tests/test_dashboard_broker.py`
- `tests/test_dashboard_repo_publish.py`
- `tests/test_dashboard_server.py`
- `tests/test_dashboard_lifecycle.py`

**Modified files:**
- `requirements.txt` — add `aiohttp>=3.9`
- `agent_hub/config.py` — add `dashboard_port: int` (default 8765, 0 disables)
- `agent_hub/tasks/repository.py` — publish task_changed / task_event after commits
- `agent_hub/tasks/gates.py` — publish gate_changed after commits
- `agent_hub/tasks/handoff_queue.py` — publish task_event after enqueue
- `agent_hub/db.py` — publish workspace_changed after set_active_workspace
- `agent_hub/__main__.py` — start/stop DashboardServer alongside orchestrator
- `.env.example` — add DASHBOARD_PORT=8765
- `README.md` — one paragraph mentioning the dashboard

---

## Task 1: Add aiohttp dependency + Event dataclasses

**Files:**
- Modify: `requirements.txt`
- Create: `agent_hub/dashboard/__init__.py`
- Create: `agent_hub/dashboard/events.py`
- Test: `tests/test_dashboard_events.py`

- [ ] **Step 1: Install aiohttp**

Add to `requirements.txt`:

```
aiohttp>=3.9
```

Then install:

```bash
.venv\Scripts\python.exe -m pip install "aiohttp>=3.9"
```

Verify:

```bash
.venv\Scripts\python.exe -c "import aiohttp; print(aiohttp.__version__)"
```

Expected: a version string ≥ 3.9.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_dashboard_events.py`:

```python
"""Tests for Event dataclasses + JSON serialization."""

from __future__ import annotations

import json

import pytest

from agent_hub.dashboard.events import (
    Event,
    TaskChanged,
    TaskEvent,
    GateChanged,
    WorkspaceChanged,
    to_json,
)


def test_task_changed_serializes():
    evt = TaskChanged(
        workspace=r"C:\dev\foo",
        task={"id": 7, "title": "t", "status": "running"},
    )
    payload = json.loads(to_json(evt))
    assert payload == {
        "kind": "task_changed",
        "workspace": r"C:\dev\foo",
        "task": {"id": 7, "title": "t", "status": "running"},
    }


def test_task_event_serializes():
    evt = TaskEvent(
        workspace=r"C:\dev\foo",
        task_id=7,
        event={"id": 42, "actor": "fullstack", "kind": "comment", "body": "ok"},
    )
    payload = json.loads(to_json(evt))
    assert payload["kind"] == "task_event"
    assert payload["task_id"] == 7
    assert payload["event"]["actor"] == "fullstack"


def test_gate_changed_serializes():
    evt = GateChanged(
        workspace=r"C:\dev\foo",
        gate={"id": 4, "task_id": 6, "kind": "design", "resolved_at": None},
    )
    payload = json.loads(to_json(evt))
    assert payload["kind"] == "gate_changed"
    assert payload["gate"]["task_id"] == 6


def test_workspace_changed_serializes():
    evt = WorkspaceChanged(workspace=r"C:\dev\foo")
    payload = json.loads(to_json(evt))
    assert payload == {"kind": "workspace_changed", "workspace": r"C:\dev\foo"}


def test_event_is_a_union_type():
    """All four event types are accepted where Event is annotated."""
    events: list[Event] = [
        TaskChanged(workspace="w", task={}),
        TaskEvent(workspace="w", task_id=1, event={}),
        GateChanged(workspace="w", gate={}),
        WorkspaceChanged(workspace="w"),
    ]
    assert len(events) == 4
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_events.py -v`
Expected: ImportError or failures — module doesn't exist.

- [ ] **Step 4: Implement the events module**

Create `agent_hub/dashboard/__init__.py` (empty file).

Create `agent_hub/dashboard/events.py`:

```python
"""Event dataclasses for the dashboard pub/sub broker.

Pure data shapes — no I/O, no state, no Telegram or aiohttp imports.
The broker publishes these; the server serializes them to JSON for SSE
delivery to the browser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Union


@dataclass(frozen=True, slots=True)
class TaskChanged:
    """A `tasks` row was created or updated."""
    workspace: str
    task: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """A `task_events` row landed (comment, tool use, handoff, etc.)."""
    workspace: str
    task_id: int
    event: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GateChanged:
    """A `gates` row was requested, resolved, or notified."""
    workspace: str
    gate: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WorkspaceChanged:
    """The active workspace switched. Clients should re-fetch /api/state."""
    workspace: str


Event = Union[TaskChanged, TaskEvent, GateChanged, WorkspaceChanged]


_KIND_BY_TYPE: dict[type, str] = {
    TaskChanged: "task_changed",
    TaskEvent: "task_event",
    GateChanged: "gate_changed",
    WorkspaceChanged: "workspace_changed",
}


def to_json(event: Event) -> str:
    """Serialize an event to a JSON string for SSE delivery.

    The wire format is `{"kind": <kind>, ...rest}` where `<kind>` lets
    the browser dispatch on the message type. The remaining fields are
    the dataclass's field/value pairs as-is.
    """
    kind = _KIND_BY_TYPE.get(type(event))
    if kind is None:
        raise TypeError(f"unknown event type: {type(event).__name__}")
    payload = {"kind": kind, **asdict(event)}
    return json.dumps(payload, default=str)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_events.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt agent_hub/dashboard/__init__.py \
    agent_hub/dashboard/events.py tests/test_dashboard_events.py
git commit -m "feat(dashboard): events module + aiohttp dependency"
```

---

## Task 2: DashboardBroker — pub/sub + snapshot

**Files:**
- Create: `agent_hub/dashboard/broker.py`
- Test: `tests/test_dashboard_broker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_broker.py`:

```python
"""Tests for DashboardBroker — pub/sub, snapshot, singleton helpers."""

from __future__ import annotations

import asyncio

import pytest

from agent_hub.dashboard.broker import (
    DashboardBroker,
    get_broker,
    set_broker,
)
from agent_hub.dashboard.events import (
    TaskChanged,
    TaskEvent,
    GateChanged,
    WorkspaceChanged,
)
from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return temp_db_path


@pytest.mark.asyncio
async def test_subscribe_then_publish_delivers_event(db_path):
    broker = DashboardBroker(db_path=db_path)
    received: list = []

    async def subscriber():
        async for event in broker.subscribe():
            received.append(event)
            if len(received) == 1:
                return

    task = asyncio.create_task(subscriber())
    await asyncio.sleep(0.01)  # let subscribe run

    evt = TaskChanged(workspace="ws", task={"id": 1})
    await broker.publish(evt)

    await asyncio.wait_for(task, timeout=1.0)
    assert received == [evt]


@pytest.mark.asyncio
async def test_multiple_subscribers_each_receive_event(db_path):
    broker = DashboardBroker(db_path=db_path)
    received_a: list = []
    received_b: list = []

    async def subscriber(bucket):
        async for event in broker.subscribe():
            bucket.append(event)
            if len(bucket) == 1:
                return

    t1 = asyncio.create_task(subscriber(received_a))
    t2 = asyncio.create_task(subscriber(received_b))
    await asyncio.sleep(0.01)

    evt = WorkspaceChanged(workspace="ws")
    await broker.publish(evt)

    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=1.0)
    assert received_a == [evt]
    assert received_b == [evt]


@pytest.mark.asyncio
async def test_full_queue_drops_subscriber(db_path):
    broker = DashboardBroker(db_path=db_path, queue_maxsize=2)
    # Subscribe but never consume — queue fills up.
    sub_iter = broker.subscribe().__aiter__()
    await asyncio.sleep(0.01)  # let subscribe register

    # Push 3 events; queue holds 2, third should drop the subscriber.
    for i in range(3):
        await broker.publish(TaskChanged(workspace="ws", task={"id": i}))

    # Subscriber should now be removed from the active set.
    assert len(broker._subscribers) == 0  # noqa: SLF001  (testing internal)


@pytest.mark.asyncio
async def test_snapshot_returns_expected_shape(db_path):
    db = Database(db_path)
    await db.set_active_workspace(r"C:\dev\foo")
    repo = TaskRepository(db_path)
    await repo.create(title="A", description="d", origin_chat_id=1)

    broker = DashboardBroker(db_path=db_path)
    snap = await broker.snapshot()
    assert "workspace" in snap
    assert "stats" in snap
    assert "active_tasks" in snap
    assert "pending_gates" in snap
    assert "recent_events" in snap
    assert snap["workspace"] == r"C:\dev\foo"


def test_get_broker_returns_none_when_unset():
    set_broker(None)
    assert get_broker() is None


def test_set_and_get_broker():
    b = DashboardBroker(db_path=None)
    set_broker(b)
    assert get_broker() is b
    set_broker(None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_broker.py -v`
Expected: ImportError — module missing.

- [ ] **Step 3: Implement the broker**

Create `agent_hub/dashboard/broker.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_broker.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/dashboard/broker.py tests/test_dashboard_broker.py
git commit -m "feat(dashboard): DashboardBroker pub/sub + snapshot + singleton"
```

---

## Task 3: Wire publish into repos + workspace_changed

**Files:**
- Modify: `agent_hub/tasks/repository.py`
- Modify: `agent_hub/tasks/gates.py`
- Modify: `agent_hub/tasks/handoff_queue.py`
- Modify: `agent_hub/db.py`
- Test: `tests/test_dashboard_repo_publish.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_repo_publish.py`:

```python
"""Verify each repo write triggers the right broker event."""

from __future__ import annotations

import pytest

from agent_hub.dashboard.broker import DashboardBroker, set_broker
from agent_hub.dashboard.events import (
    TaskChanged,
    TaskEvent,
    GateChanged,
    WorkspaceChanged,
)
from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    yield temp_db_path
    set_broker(None)


@pytest.fixture
def recording_broker(db_path):
    """A broker that records every published event for inspection."""
    class _Recording(DashboardBroker):
        def __init__(self):
            super().__init__(db_path=db_path)
            self.events: list = []

        async def publish(self, event):
            self.events.append(event)
            await super().publish(event)

    broker = _Recording()
    set_broker(broker)
    return broker


@pytest.mark.asyncio
async def test_task_create_publishes_task_changed(db_path, recording_broker):
    repo = TaskRepository(db_path)
    await repo.create(title="t", description="d", origin_chat_id=1)
    kinds = [type(e).__name__ for e in recording_broker.events]
    assert "TaskChanged" in kinds


@pytest.mark.asyncio
async def test_task_update_publishes_task_changed(db_path, recording_broker):
    repo = TaskRepository(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    recording_broker.events.clear()
    await repo.update(task.id, status=TaskStatus.PLANNING)
    assert any(isinstance(e, TaskChanged) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_task_comment_publishes_task_event(db_path, recording_broker):
    repo = TaskRepository(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    recording_broker.events.clear()
    await repo.comment(task.id, actor="architect", body="hi")
    assert any(isinstance(e, TaskEvent) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_gate_request_publishes_gate_changed(db_path, recording_broker):
    repo = TaskRepository(db_path)
    gates = GateRepository(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    recording_broker.events.clear()
    await gates.request(
        task_id=task.id, kind="design", artifact_path=None, summary="s",
    )
    assert any(isinstance(e, GateChanged) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_gate_resolve_publishes_gate_changed(db_path, recording_broker):
    repo = TaskRepository(db_path)
    gates = GateRepository(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    await gates.request(
        task_id=task.id, kind="design", artifact_path=None, summary="s",
    )
    recording_broker.events.clear()
    await gates.resolve(task_id=task.id, kind="design", resolution="approved")
    assert any(isinstance(e, GateChanged) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_handoff_enqueue_publishes_task_event(db_path, recording_broker):
    repo = TaskRepository(db_path)
    queue = HandoffQueue(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    recording_broker.events.clear()
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="hi",
    )
    assert any(isinstance(e, TaskEvent) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_set_active_workspace_publishes_workspace_changed(db_path, recording_broker):
    db = Database(db_path)
    recording_broker.events.clear()
    await db.set_active_workspace(r"C:\dev\foo")
    assert any(isinstance(e, WorkspaceChanged) for e in recording_broker.events)


@pytest.mark.asyncio
async def test_publish_skipped_when_broker_unset(db_path):
    """Without a broker installed, repos still work (test-friendly)."""
    set_broker(None)
    repo = TaskRepository(db_path)
    # Just must not raise.
    await repo.create(title="t", description="d", origin_chat_id=1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_repo_publish.py -v`
Expected: 7 failures — no publish calls exist yet. The "skipped when broker unset" test should pass already.

- [ ] **Step 3: Add publish helper for repos**

To keep the publish calls one-line and consistent, add a small helper to `agent_hub/dashboard/broker.py`. Append below the `set_broker` function:

```python
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
```

Add `from agent_hub.dashboard.events import Event` to the imports if not already present.

- [ ] **Step 4: Wire publish into `agent_hub/tasks/repository.py`**

Read `agent_hub/tasks/repository.py`. Find the `create`, `update`, and `comment` methods. After the `await conn.commit()` in each, add the appropriate publish.

For `create`:

```python
# After: await conn.commit()
# (existing code that returns the new task)
from agent_hub.dashboard.broker import publish_if_set
from agent_hub.dashboard.events import TaskChanged
workspace = await _resolve_workspace_for_task(self.db_path, new_task.id)
await publish_if_set(TaskChanged(workspace=workspace or "", task=_task_to_dict(new_task)))
```

For `update`:

```python
# After commit
from agent_hub.dashboard.broker import publish_if_set
from agent_hub.dashboard.events import TaskChanged
workspace = await _resolve_workspace_for_task(self.db_path, task_id)
await publish_if_set(TaskChanged(workspace=workspace or "", task=_task_to_dict(updated)))
```

For `comment` (which writes a `task_events` row):

```python
# After commit, with event_id and ts available
from agent_hub.dashboard.broker import publish_if_set
from agent_hub.dashboard.events import TaskEvent
workspace = await _resolve_workspace_for_task(self.db_path, task_id)
await publish_if_set(TaskEvent(
    workspace=workspace or "",
    task_id=task_id,
    event={"id": event_id, "ts": ts, "actor": actor, "kind": "comment",
           "body": body},
))
```

You need two new helpers at the bottom of `repository.py` (or in a new private module):

```python
async def _resolve_workspace_for_task(db_path, task_id: int) -> str | None:
    """Look up which workspace a task belongs to.

    Uses the worktree path → workspace (parent of worktree dir) mapping.
    If no worktree row exists yet (task pre-approval), falls back to
    the current active workspace.
    """
    import aiosqlite
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT path FROM worktrees WHERE task_id = ?", (task_id,)
        )
        row = await cur.fetchone()
        if row is not None:
            from pathlib import Path
            return str(Path(row["path"]).parent.parent)
        cur = await conn.execute(
            "SELECT value FROM settings_kv WHERE key = 'active_workspace'"
        )
        row = await cur.fetchone()
        return row["value"] if row else None


def _task_to_dict(task) -> dict:
    """Serialize a Task model to a plain dict for SSE delivery."""
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": str(task.status.value) if hasattr(task.status, "value") else str(task.status),
        "owner": task.owner,
        "worktree_path": task.worktree_path,
        "branch_name": task.branch_name,
        "created_at": str(task.created_at) if task.created_at else None,
        "updated_at": str(task.updated_at) if task.updated_at else None,
        "cost_usd_total": float(task.cost_usd_total or 0),
    }
```

Adjust the field references if `repository.py`'s Task model uses different attribute names — read the file first to confirm.

- [ ] **Step 5: Wire publish into `agent_hub/tasks/gates.py`**

Read `agent_hub/tasks/gates.py`. After each `await conn.commit()` in `request`, `resolve`, and `notify`, add:

```python
from agent_hub.dashboard.broker import publish_if_set
from agent_hub.dashboard.events import GateChanged
from agent_hub.tasks.repository import _resolve_workspace_for_task
workspace = await _resolve_workspace_for_task(self.db_path, task_id)
await publish_if_set(GateChanged(
    workspace=workspace or "",
    gate={"id": gate_id, "task_id": task_id, "kind": kind,
          "resolved_at": resolved_at, "requested_at": requested_at},
))
```

Use the variables available in scope at each call site — `request` has `requested_at`, `resolve` has `resolved_at`, `notify` has a `notified_at`. Pass whichever fields exist; the dict can be partial.

- [ ] **Step 6: Wire publish into `agent_hub/tasks/handoff_queue.py`**

After the `await conn.commit()` in `enqueue`:

```python
from agent_hub.dashboard.broker import publish_if_set
from agent_hub.dashboard.events import TaskEvent
from agent_hub.tasks.repository import _resolve_workspace_for_task
workspace = await _resolve_workspace_for_task(self.db_path, task_id)
await publish_if_set(TaskEvent(
    workspace=workspace or "",
    task_id=task_id,
    event={"id": cur.lastrowid, "ts": _utcnow_iso(), "actor": from_agent,
           "kind": "handoff",
           "body": f"→ {to_agent}: {message[:200]}"},
))
```

- [ ] **Step 7: Wire publish into `agent_hub/db.py:Database.set_active_workspace`**

In `Database.set_active_workspace`, after the existing `await conn.commit()` (which happens inside `set_kv` and `_touch_recent`):

```python
async def set_active_workspace(self, path: str) -> None:
    await self.set_kv(self._ACTIVE_KEY, path)
    await self._touch_recent(path)
    # Notify the dashboard
    from agent_hub.dashboard.broker import publish_if_set
    from agent_hub.dashboard.events import WorkspaceChanged
    await publish_if_set(WorkspaceChanged(workspace=path))
```

- [ ] **Step 8: Run tests**

Run: `pytest tests/test_dashboard_repo_publish.py -v`
Expected: all 8 tests pass.

Run: `pytest tests/test_tasks_repository.py tests/test_gates.py tests/test_handoff_queue.py -q`
Expected: existing tests still pass (no regressions).

- [ ] **Step 9: Commit**

```bash
git add agent_hub/dashboard/broker.py \
    agent_hub/tasks/repository.py \
    agent_hub/tasks/gates.py \
    agent_hub/tasks/handoff_queue.py \
    agent_hub/db.py \
    tests/test_dashboard_repo_publish.py
git commit -m "feat(dashboard): publish events from repos + workspace_changed"
```

---

## Task 4: DashboardServer — aiohttp routes + SSE

**Files:**
- Create: `agent_hub/dashboard/server.py`
- Test: `tests/test_dashboard_server.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_server.py`:

```python
"""Tests for DashboardServer — HTTP routes + SSE."""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from agent_hub.dashboard.broker import DashboardBroker
from agent_hub.dashboard.events import TaskChanged
from agent_hub.dashboard.server import DashboardServer
from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def server_and_db(temp_db_path, unused_tcp_port):
    db = Database(temp_db_path)
    await db.init()
    await db.set_active_workspace(r"C:\dev\foo")

    broker = DashboardBroker(db_path=temp_db_path)
    server = DashboardServer(
        broker=broker, db_path=temp_db_path, port=unused_tcp_port,
    )
    await server.start()
    yield server, broker, temp_db_path, unused_tcp_port
    await server.stop()


@pytest.fixture
def unused_tcp_port():
    """Return a TCP port that's free at the moment we call."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_get_root_returns_html(server_and_db):
    _, _, _, port = server_and_db
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/") as resp:
            assert resp.status == 200
            text = await resp.text()
            assert "AGENT-HUB" in text or "<html" in text.lower()


@pytest.mark.asyncio
async def test_get_state_returns_json_shape(server_and_db):
    _, _, _, port = server_and_db
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/api/state") as resp:
            assert resp.status == 200
            payload = await resp.json()
            for key in ("workspace", "stats", "active_tasks",
                        "pending_gates", "recent_events"):
                assert key in payload


@pytest.mark.asyncio
async def test_get_task_returns_timeline(server_and_db):
    _, _, db_path, port = server_and_db
    repo = TaskRepository(db_path)
    task = await repo.create(title="t", description="d", origin_chat_id=1)
    await repo.comment(task.id, actor="architect", body="design here")

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"http://127.0.0.1:{port}/api/task/{task.id}"
        ) as resp:
            assert resp.status == 200
            payload = await resp.json()
            assert payload["task"]["id"] == task.id
            assert len(payload["events"]) >= 1


@pytest.mark.asyncio
async def test_get_task_404_for_unknown(server_and_db):
    _, _, _, port = server_and_db
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/api/task/9999") as resp:
            assert resp.status == 404


@pytest.mark.asyncio
async def test_sse_delivers_published_event(server_and_db):
    _, broker, _, port = server_and_db
    received: list = []

    async def consume():
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{port}/api/events",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                async for raw in resp.content:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: "):
                        received.append(json.loads(line[6:]))
                        if len(received) >= 1:
                            return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.1)  # let connection open

    await broker.publish(TaskChanged(
        workspace=r"C:\dev\foo", task={"id": 1, "title": "t"},
    ))

    await asyncio.wait_for(consumer, timeout=4.0)
    assert received[0]["kind"] == "task_changed"
    assert received[0]["task"]["id"] == 1


@pytest.mark.asyncio
async def test_sse_filters_other_workspaces(server_and_db):
    _, broker, _, port = server_and_db
    received: list = []
    done = asyncio.Event()

    async def consume():
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{port}/api/events",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                async for raw in resp.content:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("data: "):
                        received.append(json.loads(line[6:]))
                        done.set()
                        return

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.1)

    # Publish an event from a DIFFERENT workspace — should be filtered.
    await broker.publish(TaskChanged(
        workspace=r"C:\other-workspace", task={"id": 1},
    ))
    # Then publish a matching one.
    await broker.publish(TaskChanged(
        workspace=r"C:\dev\foo", task={"id": 2},
    ))

    await asyncio.wait_for(done.wait(), timeout=4.0)
    consumer.cancel()
    # Only the matching workspace's event arrived.
    assert all(
        evt.get("workspace") == r"C:\dev\foo"
        for evt in received
        if "workspace" in evt
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_server.py -v`
Expected: ImportError — server module doesn't exist.

- [ ] **Step 3: Implement the server**

Create `agent_hub/dashboard/server.py`:

```python
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

import aiohttp
import aiosqlite
import structlog
from aiohttp import web
from dataclasses import asdict

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard_server.py -v`
Expected: 6 passed. Tests use real aiohttp HTTP calls to a real bound socket.

If `test_sse_filters_other_workspaces` is flaky on Windows, add a `await asyncio.sleep(0.05)` between the two publishes.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/dashboard/server.py tests/test_dashboard_server.py
git commit -m "feat(dashboard): aiohttp server with 4 routes + SSE + workspace filter"
```

---

## Task 5: Frontend HTML/CSS — Mission Console static page

**Files:**
- Create: `agent_hub/dashboard/static/index.html`

- [ ] **Step 1: Create the index.html**

Create `agent_hub/dashboard/static/index.html`. This is the full file — paste it verbatim. The CSS animations (sweeping scanline, pulsing dots, sliding bars, streaming feed rows, count-up) are all driven by `@keyframes`. JS is added in Task 6 — for now the page renders with placeholder content so the route test passes.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AGENT-HUB.LIVE</title>
  <style>
    :root {
      --bg-deep: #060a14;
      --bg-mid: #0a1020;
      --cyan: #00c8ff;
      --cyan-glow: #00ffae;
      --amber: #ffd84d;
      --magenta: #ff5d6c;
      --text: #d8e3f5;
      --text-dim: #8aa0c5;
      --grid: rgba(0, 200, 255, 0.06);
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0;
      background: linear-gradient(180deg, var(--bg-deep) 0%, var(--bg-mid) 100%);
      color: var(--text);
      font-family: "SF Mono", "Cascadia Code", "JetBrains Mono", ui-monospace, monospace;
      min-height: 100vh;
      overflow-x: hidden;
    }
    /* Background grid */
    body::before {
      content: '';
      position: fixed; inset: 0;
      background-image:
        linear-gradient(var(--grid) 1px, transparent 1px),
        linear-gradient(90deg, var(--grid) 1px, transparent 1px);
      background-size: 32px 32px;
      pointer-events: none;
      z-index: 0;
    }
    /* Sweeping scanline */
    body::after {
      content: '';
      position: fixed; inset: 0;
      background: linear-gradient(180deg, transparent 48%, rgba(0,255,200,0.06) 50%, transparent 52%);
      pointer-events: none;
      animation: scan-sweep 6s linear infinite;
      z-index: 2;
    }
    @keyframes scan-sweep {
      0% { transform: translateY(-100%); opacity: 0; }
      10%, 90% { opacity: 0.5; }
      100% { transform: translateY(100%); opacity: 0; }
    }
    main { position: relative; z-index: 1; max-width: 1200px; margin: 0 auto; padding: 24px; }

    /* Header */
    header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 18px; padding-bottom: 12px; border-bottom: 1px solid rgba(0,200,255,0.15); }
    .brand { font-size: 18px; font-weight: 700; letter-spacing: 3px; color: var(--cyan); }
    .workspace { color: var(--text-dim); font-size: 12px; }
    .connection { color: var(--cyan-glow); font-size: 11px; letter-spacing: 1.5px; display: flex; align-items: center; gap: 6px; }
    .connection .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--cyan-glow); box-shadow: 0 0 8px var(--cyan-glow); }
    .connection.disconnected .dot { background: var(--magenta); box-shadow: 0 0 8px var(--magenta); }

    /* HUD strip */
    .hud-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
    .hud-cell { border: 1px solid rgba(0,200,255,0.2); padding: 10px 14px; background: rgba(0,200,255,0.04); position: relative; }
    .hud-cell .lbl { font-size: 9px; letter-spacing: 2px; color: rgba(0,200,255,0.7); }
    .hud-cell .val { font-size: 24px; font-weight: 700; color: #fff; font-feature-settings: 'tnum'; }
    .hud-cell.warn .val { color: var(--amber); text-shadow: 0 0 8px rgba(255,216,77,0.5); }
    .hud-cell.good .val { color: var(--cyan-glow); text-shadow: 0 0 8px rgba(0,255,174,0.4); }
    /* Cost cell: hidden by default, expands on hover */
    .hud-cell.cost { cursor: help; }
    .hud-cell.cost .val { font-size: 18px; }
    .hud-cell.cost .detail { font-size: 11px; color: var(--text-dim); margin-top: 4px; max-height: 0; opacity: 0; overflow: hidden; transition: max-height 0.25s ease-out, opacity 0.25s ease-out; }
    .hud-cell.cost:hover .detail { max-height: 60px; opacity: 1; }
    @keyframes counter-up {
      0% { transform: translateY(8px); opacity: 0; }
      100% { transform: translateY(0); opacity: 1; }
    }
    .hud-cell .val.counting { animation: counter-up 0.4s cubic-bezier(0.4, 0, 0.2, 1); }

    /* Panels */
    .panel { border: 1px solid rgba(0,200,255,0.18); padding: 12px 16px; background: rgba(0,200,255,0.03); margin-bottom: 14px; }
    .panel h4 { font-size: 10px; letter-spacing: 3px; color: var(--cyan); font-weight: 600; margin: 0 0 10px; text-transform: uppercase; }
    .panel.pending h4 { color: var(--amber); text-shadow: 0 0 6px rgba(255,216,77,0.4); }

    /* Task rows */
    .task-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px dashed rgba(0,200,255,0.12); font-size: 13px; cursor: pointer; transition: background 0.15s; }
    .task-row:last-child { border-bottom: none; }
    .task-row:hover { background: rgba(0,200,255,0.05); }
    .blink { width: 8px; height: 8px; background: var(--cyan-glow); box-shadow: 0 0 10px var(--cyan-glow); animation: pulse-dot 1s ease-in-out infinite; flex-shrink: 0; }
    .blink.warn { background: var(--amber); box-shadow: 0 0 10px var(--amber); }
    .blink.danger { background: var(--magenta); box-shadow: 0 0 10px var(--magenta); }
    @keyframes pulse-dot {
      0%, 100% { transform: scale(1); opacity: 1; }
      50% { transform: scale(1.4); opacity: 0.7; }
    }
    .task-row .id { color: var(--cyan); font-weight: 700; }
    .task-row .name { color: #fff; flex: 1; }
    .task-row .agent { color: rgba(0,200,255,0.7); font-size: 11px; letter-spacing: 1px; }
    .task-row .elapsed { color: var(--text-dim); font-size: 11px; font-variant-numeric: tabular-nums; }
    .task-row .bar-track { width: 60px; height: 4px; background: rgba(0,200,255,0.15); position: relative; overflow: hidden; }
    .task-row .bar { position: absolute; top: 0; bottom: 0; left: 0; background: linear-gradient(90deg, var(--cyan), var(--cyan-glow)); width: 50%; animation: loading-bar 2.4s ease-in-out infinite; }
    @keyframes loading-bar {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(300%); }
    }

    /* Inline-expand for task detail */
    .task-detail { max-height: 0; overflow: hidden; transition: max-height 0.3s ease-out; padding-left: 22px; }
    .task-row.expanded + .task-detail { max-height: 600px; padding-bottom: 12px; }
    .task-detail .timeline-row { display: flex; gap: 12px; padding: 5px 0; font-size: 12px; border-bottom: 1px dotted rgba(0,200,255,0.08); }
    .task-detail .timeline-row .ts { color: var(--text-dim); font-variant-numeric: tabular-nums; min-width: 70px; }
    .task-detail .timeline-row .actor { color: var(--amber); font-weight: 600; min-width: 80px; }

    /* Pending */
    .pending-row { padding: 10px 0; border-bottom: 1px dashed rgba(255,216,77,0.18); font-size: 13px; }
    .pending-row:last-child { border-bottom: none; }
    .pending-row .alert { color: var(--magenta); text-shadow: 0 0 8px rgba(255,93,108,0.6); font-weight: 700; letter-spacing: 1px; }

    /* Stream feed */
    .feed-line { padding: 4px 0; font-size: 11.5px; display: flex; gap: 12px; align-items: baseline; }
    .feed-line.fade-in { animation: streamline 0.4s ease-out; }
    @keyframes streamline {
      from { opacity: 0; transform: translateX(-8px); }
      to { opacity: 1; transform: translateX(0); }
    }
    .feed-line .t { color: rgba(0,200,255,0.7); font-variant-numeric: tabular-nums; }
    .feed-line .src { color: var(--amber); min-width: 80px; }
    .feed-line .msg { color: var(--text); }
    .feed-line .msg code { background: rgba(0,200,255,0.08); padding: 1px 5px; border-radius: 0; font-family: inherit; }

    /* Empty states */
    .empty { color: var(--text-dim); font-size: 12px; padding: 12px 0; text-align: center; font-style: italic; }

    /* Reduced motion */
    @media (prefers-reduced-motion: reduce) {
      body::after, .blink, .bar, .feed-line.fade-in, .hud-cell .val.counting {
        animation: none !important;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div class="brand">AGENT-HUB.LIVE</div>
      <div class="workspace" id="workspace-label">workspace: —</div>
      <div class="connection" id="conn"><span class="dot"></span> CONNECTING…</div>
    </header>

    <div class="hud-strip">
      <div class="hud-cell" data-key="running">
        <div class="lbl">RUNNING</div>
        <div class="val" id="stat-running">—</div>
      </div>
      <div class="hud-cell warn" data-key="pending">
        <div class="lbl">PENDING</div>
        <div class="val" id="stat-pending">—</div>
      </div>
      <div class="hud-cell good" data-key="done24h">
        <div class="lbl">DONE 24H</div>
        <div class="val" id="stat-done24h">—</div>
      </div>
      <div class="hud-cell cost" data-key="cost">
        <div class="lbl">$ HOVER</div>
        <div class="val" id="stat-cost">$—</div>
        <div class="detail" id="cost-detail"></div>
      </div>
    </div>

    <section class="panel" id="active-panel">
      <h4>›› ACTIVE</h4>
      <div id="active-list"><div class="empty">No active tasks.</div></div>
    </section>

    <section class="panel pending" id="pending-panel">
      <h4>›› PENDING DECISION</h4>
      <div id="pending-list"><div class="empty">No pending gates.</div></div>
    </section>

    <section class="panel" id="stream-panel">
      <h4>›› TELEMETRY STREAM</h4>
      <div id="event-stream"><div class="empty">Waiting for events…</div></div>
    </section>
  </main>

  <script>
    /* JS bootstrap is added in Task 6. */
  </script>
</body>
</html>
```

- [ ] **Step 2: Verify the file is served**

Run: `pytest tests/test_dashboard_server.py::test_get_root_returns_html -v`
Expected: PASS (the test asserts the response contains "AGENT-HUB" — the new HTML satisfies this).

- [ ] **Step 3: Commit**

```bash
git add agent_hub/dashboard/static/index.html
git commit -m "feat(dashboard): Mission Console HTML + CSS (static)"
```

---

## Task 6: Frontend JS — bootstrap, SSE, DOM updates, inline expand

**Files:**
- Modify: `agent_hub/dashboard/static/index.html` (replace the empty `<script>` block)

- [ ] **Step 1: Replace the `<script>` block in `index.html`**

In `agent_hub/dashboard/static/index.html`, find the line:

```html
    /* JS bootstrap is added in Task 6. */
```

Replace the entire `<script>...</script>` block with:

```html
  <script>
  (function () {
    'use strict';

    const $ = (id) => document.getElementById(id);

    // ---- DOM refs ----
    const elConn = $('conn');
    const elWorkspace = $('workspace-label');
    const elActive = $('active-list');
    const elPending = $('pending-list');
    const elStream = $('event-stream');
    const stats = {
      running:  $('stat-running'),
      pending:  $('stat-pending'),
      done24h:  $('stat-done24h'),
      cost:     $('stat-cost'),
      detail:   $('cost-detail'),
    };

    // ---- State ----
    let snapshot = null;
    let expandedTaskId = null;
    let backoffMs = 500;

    // ---- Render ----
    function setStat(el, value) {
      if (el.textContent === String(value)) return;
      el.classList.remove('counting');
      // Trigger reflow so the animation re-runs.
      void el.offsetWidth;
      el.textContent = value;
      el.classList.add('counting');
    }

    function renderHeader(snap) {
      elWorkspace.textContent = snap.workspace
        ? `workspace: ${snap.workspace}`
        : 'workspace: —';
    }

    function renderStats(snap) {
      const s = snap.stats || {};
      setStat(stats.running, formatTwoDigit(s.running));
      setStat(stats.pending, formatTwoDigit(s.pending));
      setStat(stats.done24h, formatTwoDigit(s.done_24h));
      // Cost aggregate from active tasks (workspace already filters).
      const totalCost = (snap.active_tasks || []).reduce(
        (sum, t) => sum + (t.cost_usd_total || 0), 0
      );
      setStat(stats.cost, totalCost > 0 ? `$${totalCost.toFixed(2)}` : '$—');
      stats.detail.textContent = (snap.active_tasks || [])
        .filter(t => t.cost_usd_total > 0)
        .map(t => `#${t.id} $${t.cost_usd_total.toFixed(2)}`)
        .join(' · ') || 'no spend yet';
    }

    function formatTwoDigit(n) {
      const num = Number(n || 0);
      return num < 10 ? '0' + num : String(num);
    }

    function renderActiveTasks(snap) {
      const tasks = snap.active_tasks || [];
      if (tasks.length === 0) {
        elActive.innerHTML = '<div class="empty">No active tasks.</div>';
        return;
      }
      elActive.innerHTML = tasks.map(t => renderTaskRow(t)).join('');
      // Re-attach click handlers.
      elActive.querySelectorAll('.task-row').forEach(row => {
        row.addEventListener('click', () => toggleExpand(Number(row.dataset.taskId)));
      });
    }

    function renderTaskRow(task) {
      const isExpanded = task.id === expandedTaskId;
      return `
        <div class="task-row ${isExpanded ? 'expanded' : ''}" data-task-id="${task.id}">
          <span class="blink"></span>
          <span class="id">#${task.id}</span>
          <span class="name">${escapeHtml(task.title || '')}</span>
          <span class="agent">${escapeHtml((task.owner || '').toUpperCase())}</span>
          <span class="bar-track"><span class="bar" style="animation-delay:${(task.id % 5) * -0.4}s"></span></span>
          <span class="elapsed">${elapsedFrom(task.updated_at)}</span>
        </div>
        <div class="task-detail" data-task-id="${task.id}">
          ${isExpanded ? '<div class="empty">Loading timeline…</div>' : ''}
        </div>
      `;
    }

    function renderPending(snap) {
      const gates = snap.pending_gates || [];
      if (gates.length === 0) {
        elPending.innerHTML = '<div class="empty">No pending gates.</div>';
        return;
      }
      elPending.innerHTML = gates.map(g => `
        <div class="pending-row">
          <span class="blink warn"></span>
          <span class="id" style="color:var(--amber)">#${g.task_id}</span>
          <span class="name">${escapeHtml(g.title || '')}</span>
          <span class="alert">/APPROVE ${g.task_id}</span>
          <span class="elapsed">${elapsedFrom(g.requested_at)}</span>
        </div>
      `).join('');
    }

    function renderStream(snap) {
      const events = (snap.recent_events || []).slice(0, 50);
      if (events.length === 0) {
        elStream.innerHTML = '<div class="empty">Waiting for events…</div>';
        return;
      }
      elStream.innerHTML = events.map(e => renderEventRow(e)).join('');
    }

    function renderEventRow(evt) {
      const ts = (evt.ts || '').replace('T', ' ').slice(11, 19);
      let body = '';
      try {
        const payload = typeof evt.payload_json === 'string'
          ? JSON.parse(evt.payload_json) : (evt.payload_json || {});
        body = escapeHtml(payload.body || payload.message || evt.kind || '');
      } catch {
        body = escapeHtml(evt.kind || '');
      }
      return `
        <div class="feed-line">
          <span class="t">[${ts}]</span>
          <span class="src">${escapeHtml((evt.actor || '').toUpperCase())}</span>
          <span class="msg">${body}</span>
        </div>
      `;
    }

    function prependStreamRow(evt) {
      const empty = elStream.querySelector('.empty');
      if (empty) empty.remove();
      const wrapper = document.createElement('div');
      wrapper.innerHTML = renderEventRow(evt);
      const newRow = wrapper.firstElementChild;
      newRow.classList.add('fade-in');
      elStream.insertBefore(newRow, elStream.firstChild);
      // Cap at 50.
      while (elStream.children.length > 50) {
        elStream.removeChild(elStream.lastChild);
      }
    }

    function elapsedFrom(iso) {
      if (!iso) return '—';
      const then = Date.parse(iso);
      if (Number.isNaN(then)) return '—';
      const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
      if (seconds < 60) return seconds + 's';
      if (seconds < 3600) return Math.floor(seconds / 60) + 'm';
      return Math.floor(seconds / 3600) + 'h';
    }

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
      }[c]));
    }

    // ---- Inline expand ----
    async function toggleExpand(taskId) {
      if (expandedTaskId === taskId) {
        expandedTaskId = null;
        renderActiveTasks(snapshot);
        return;
      }
      expandedTaskId = taskId;
      renderActiveTasks(snapshot);

      try {
        const resp = await fetch(`/api/task/${taskId}`);
        if (!resp.ok) return;
        const data = await resp.json();
        const detail = document.querySelector(
          `.task-detail[data-task-id="${taskId}"]`
        );
        if (!detail) return;
        detail.innerHTML = data.events.map(e => {
          let body = '';
          try {
            const p = typeof e.payload_json === 'string'
              ? JSON.parse(e.payload_json) : (e.payload_json || {});
            body = escapeHtml(p.body || p.message || e.kind || '');
          } catch {
            body = escapeHtml(e.kind || '');
          }
          const ts = (e.ts || '').replace('T', ' ').slice(0, 19);
          return `
            <div class="timeline-row">
              <span class="ts">${ts}</span>
              <span class="actor">${escapeHtml((e.actor || '').toUpperCase())}</span>
              <span>${body}</span>
            </div>
          `;
        }).join('') || '<div class="empty">No events yet.</div>';
      } catch (err) {
        // Silent — keep the row expanded with "Loading…" text.
      }
    }

    // ---- Connection ----
    function setConnected(ok) {
      if (ok) {
        elConn.classList.remove('disconnected');
        elConn.innerHTML = '<span class="dot"></span> ONLINE';
      } else {
        elConn.classList.add('disconnected');
        elConn.innerHTML = '<span class="dot"></span> RECONNECTING…';
      }
    }

    async function loadSnapshot() {
      try {
        const resp = await fetch('/api/state');
        if (!resp.ok) throw new Error('snapshot failed');
        snapshot = await resp.json();
        renderHeader(snapshot);
        renderStats(snapshot);
        renderActiveTasks(snapshot);
        renderPending(snapshot);
        renderStream(snapshot);
      } catch (err) {
        // Retry on next reconnect.
      }
    }

    function applyEvent(evt) {
      if (!snapshot) return;
      if (evt.kind === 'workspace_changed') {
        // Re-snapshot — workspace filter on the server side changes too.
        loadSnapshot();
        return;
      }
      if (evt.kind === 'task_changed') {
        // Replace existing task entry or insert new one.
        const list = snapshot.active_tasks = snapshot.active_tasks || [];
        const idx = list.findIndex(t => t.id === evt.task.id);
        const isTerminal = ['done', 'blocked', 'cancelled']
          .includes(evt.task.status);
        if (isTerminal && idx >= 0) {
          list.splice(idx, 1);
        } else if (idx >= 0) {
          list[idx] = evt.task;
        } else if (!isTerminal) {
          list.unshift(evt.task);
        }
        renderActiveTasks(snapshot);
        renderStats(snapshot);
        return;
      }
      if (evt.kind === 'gate_changed') {
        // Pending if resolved_at is falsy.
        const list = snapshot.pending_gates = snapshot.pending_gates || [];
        const idx = list.findIndex(g => g.id === evt.gate.id);
        if (evt.gate.resolved_at) {
          if (idx >= 0) list.splice(idx, 1);
        } else if (idx >= 0) {
          list[idx] = evt.gate;
        } else {
          list.push(evt.gate);
        }
        renderPending(snapshot);
        renderStats(snapshot);
        return;
      }
      if (evt.kind === 'task_event') {
        const list = snapshot.recent_events = snapshot.recent_events || [];
        list.unshift(evt.event);
        snapshot.recent_events = list.slice(0, 50);
        prependStreamRow(evt.event);
        return;
      }
    }

    function connectSSE() {
      const es = new EventSource('/api/events');
      es.onopen = () => {
        backoffMs = 500;
        setConnected(true);
      };
      es.onerror = () => {
        es.close();
        setConnected(false);
        setTimeout(() => {
          loadSnapshot();
          connectSSE();
        }, backoffMs);
        backoffMs = Math.min(backoffMs * 4, 8000);
      };
      es.onmessage = (msg) => {
        try {
          applyEvent(JSON.parse(msg.data));
        } catch {
          // Ignore malformed.
        }
      };
    }

    // ---- Boot ----
    loadSnapshot().then(connectSSE);
  })();
  </script>
```

- [ ] **Step 2: Verify the file still serves**

Run: `pytest tests/test_dashboard_server.py -v`
Expected: all 6 tests still pass.

- [ ] **Step 3: Commit**

```bash
git add agent_hub/dashboard/static/index.html
git commit -m "feat(dashboard): JS bootstrap — SSE wiring + DOM updates + inline expand"
```

---

## Task 7: Lifecycle wiring — config + __main__ + integration smoke

**Files:**
- Modify: `agent_hub/config.py`
- Modify: `agent_hub/__main__.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_dashboard_lifecycle.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard_lifecycle.py`:

```python
"""Tests for dashboard startup lifecycle (config + bind handling)."""

from __future__ import annotations

import socket

import pytest

from agent_hub.dashboard.broker import DashboardBroker
from agent_hub.dashboard.server import DashboardServer
from agent_hub.db import Database


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return temp_db_path


@pytest.fixture
def unused_tcp_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_start_binds_to_port(db_path, unused_tcp_port):
    server = DashboardServer(
        broker=DashboardBroker(db_path=db_path),
        db_path=db_path,
        port=unused_tcp_port,
    )
    await server.start()
    # Port should be in use now — second bind should fail.
    s = socket.socket()
    with pytest.raises(OSError):
        s.bind(("127.0.0.1", unused_tcp_port))
    s.close()
    await server.stop()


@pytest.mark.asyncio
async def test_stop_releases_port(db_path, unused_tcp_port):
    server = DashboardServer(
        broker=DashboardBroker(db_path=db_path),
        db_path=db_path,
        port=unused_tcp_port,
    )
    await server.start()
    await server.stop()
    # Port should be free now.
    s = socket.socket()
    s.bind(("127.0.0.1", unused_tcp_port))
    s.close()


@pytest.mark.asyncio
async def test_port_conflict_does_not_raise(db_path, unused_tcp_port):
    """If the port is already in use, start() logs and continues."""
    # Hold the port.
    holder = socket.socket()
    holder.bind(("127.0.0.1", unused_tcp_port))
    holder.listen(1)
    try:
        server = DashboardServer(
            broker=DashboardBroker(db_path=db_path),
            db_path=db_path,
            port=unused_tcp_port,
        )
        # Must not raise.
        await server.start()
        # Cleanup is safe even when bind failed.
        await server.stop()
    finally:
        holder.close()


def test_settings_includes_dashboard_port(monkeypatch):
    """Settings exposes dashboard_port with a sensible default."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "1")
    monkeypatch.delenv("DASHBOARD_PORT", raising=False)
    from agent_hub.config import load_settings
    s = load_settings()
    assert s.dashboard_port == 8765


def test_dashboard_port_can_be_overridden(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "1")
    monkeypatch.setenv("DASHBOARD_PORT", "9000")
    from agent_hub.config import load_settings
    s = load_settings()
    assert s.dashboard_port == 9000


def test_dashboard_port_zero_disables(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "1")
    monkeypatch.setenv("DASHBOARD_PORT", "0")
    from agent_hub.config import load_settings
    s = load_settings()
    assert s.dashboard_port == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard_lifecycle.py -v`
Expected: failures for `test_settings_*` (field doesn't exist yet); the other tests pass if Tasks 4-5 are complete.

- [ ] **Step 3: Add `dashboard_port` to `agent_hub/config.py`**

In the `Settings` class, add the field after `log_level`:

```python
    # Dashboard
    dashboard_port: int = Field(
        default=8765,
        ge=0,
        description=(
            "Port for the bundled live dashboard (http://localhost:<port>). "
            "Set to 0 to disable."
        ),
    )
```

In `load_settings`, add:

```python
        dashboard_port=int(os.getenv("DASHBOARD_PORT", "8765")),
```

- [ ] **Step 4: Wire dashboard into `agent_hub/__main__.py`**

Find the section where the orchestrator is started (look for `await orchestrator.start()`). Just before that, add the dashboard startup. The exact insertion site is in the function that's called as part of `application.post_init` (search for `_post_init`).

Add to the imports at the top of `__main__.py`:

```python
from agent_hub.dashboard.broker import DashboardBroker, set_broker
from agent_hub.dashboard.server import DashboardServer
```

In `_post_init` (or wherever `await orchestrator.start()` is), just before that line, add:

```python
    # Dashboard (optional — DASHBOARD_PORT=0 disables)
    dashboard_server = None
    if settings.dashboard_port > 0:
        broker = DashboardBroker(db_path=settings.database_path)
        set_broker(broker)
        dashboard_server = DashboardServer(
            broker=broker,
            db_path=settings.database_path,
            port=settings.dashboard_port,
        )
        await dashboard_server.start()

    # Stash for shutdown.
    app.bot_data["dashboard_server"] = dashboard_server
```

Adjust the variable scoping — `settings`, `app`, etc., must be in scope. Read the surrounding function first to confirm.

In `_post_shutdown` (or whichever shutdown coroutine exists), add to the START of the function:

```python
    dashboard_server = app.bot_data.get("dashboard_server")
    if dashboard_server is not None:
        await dashboard_server.stop()
    set_broker(None)
```

- [ ] **Step 5: Update `.env.example`**

Append to `.env.example`:

```
# Dashboard (live activity monitor at http://localhost:<port>)
# Set to 0 to disable.
DASHBOARD_PORT=8765
```

- [ ] **Step 6: Add README mention**

Find the existing "Talking to the team" or "Quick start" section in `README.md`. Add a new short section:

```markdown
## Live dashboard

While the bot is running, open <http://localhost:8765> for a live read-only
view of active tasks, pending gates, and the agent activity stream.
Localhost only — set `DASHBOARD_PORT=0` in `.env` to disable.
```

- [ ] **Step 7: Run all tests**

Run: `pytest tests/ -q --ignore=tests/smoke`
Expected: every test passes (including the new dashboard tests + no regressions).

- [ ] **Step 8: Manual sanity check (recommended, not required for commit)**

Start the bot:

```bash
.venv\Scripts\python.exe -m agent_hub
```

Open <http://localhost:8765> in a browser. You should see the Mission Console page. Send `/start` to the bot and verify:

- The header reflects the active workspace.
- The connection indicator is green.
- File a task; the active list updates live.
- The telemetry stream shows agent activity in real time.

Stop the bot with Ctrl-C; the dashboard exits cleanly.

- [ ] **Step 9: Commit**

```bash
git add agent_hub/config.py agent_hub/__main__.py \
    .env.example README.md tests/test_dashboard_lifecycle.py
git commit -m "feat(dashboard): lifecycle wiring + config + README"
```

---

## Final verification

```bash
cd C:\dev\agent-hub
.venv\Scripts\python.exe -m pytest -q --ignore=tests/smoke
```

Expected: full suite passes (existing 389 + ~25 new dashboard tests).
