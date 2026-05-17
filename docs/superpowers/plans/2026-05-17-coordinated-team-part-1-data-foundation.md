# Coordinated team — Part 1: Data foundation + MCP server skeleton

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the SQLite schema, state machine, repositories, and stdio MCP server that exposes `tasks.*`, `handoff`, and `gate.*` tools. End state: an MCP server you can launch standalone, call all tools against, and verify with passing unit tests.

**Architecture:** SQLite (WAL) as the single source of truth; pydantic models on top; a stdio MCP server registers tools that thin-wrap the repositories. Status transitions go through a single typed validator. Zero coupling to the runner, orchestrator, or Telegram bot — those land in later plans.

**Tech Stack:** Python 3.14, aiosqlite, pydantic v2, `mcp` SDK (already pulled in by claude-agent-sdk), pytest + pytest-asyncio + pytest-xdist.

**Source spec:** `docs/superpowers/specs/2026-05-17-coordinated-agent-team-design.md` (sections 4.1–4.3, 7 Tier 1)

**Not in this plan (deferred):** worktree manager, runner changes, orchestrator, Telegram commands, agent prompt updates, integration tests (Tier 2), smoke tests (Tier 3), spend tracking (needs runner TurnDone events).

---

## File structure produced by this plan

```
agent_hub/
  db.py                       # MODIFY: add new tables + WAL pragma in init()
  state_machine.py            # CREATE: TRANSITIONS map + validate()
  tasks/                      # CREATE: data-layer package
    __init__.py
    models.py                 # Task, TaskEvent, Gate, HandoffRow pydantic models
    repository.py             # CRUD: create, get, list, tree, update, comment
    handoff_queue.py          # enqueue + atomic claim
    gates.py                  # request, resolve, status
  mcp_server/                 # CREATE: stdio MCP server package
    __init__.py
    __main__.py               # python -m agent_hub.mcp_server entrypoint
    server.py                 # MCP server setup, dependency injection of DB
    tools/
      __init__.py
      tasks_tools.py          # 6 tasks.* tools
      handoff_tool.py         # handoff tool
      gate_tools.py           # 2 gate.* tools

tests/
  __init__.py
  conftest.py                 # CREATE: temp_db fixture + frozen_clock
  test_schema.py              # tables exist, WAL on, FKs on
  test_state_machine.py       # transition map allowed/disallowed
  test_tasks_repository.py    # create/get/list/tree/update/comment
  test_handoff_queue.py       # enqueue + atomic claim race
  test_gates.py               # request/resolve/status lifecycle
  test_mcp_tools.py           # each tool: happy path + validation errors
  test_mcp_server_e2e.py      # subprocess launch + stdio round-trip
```

---

## Conventions used in every task

- **TDD pattern:** failing test → verify it fails → minimal implementation → verify it passes → commit.
- **Test runner:** `pytest` from repo root. Use `-x` to stop on first failure, `-v` for verbose, `-k name` to target.
- **Commit style:** Conventional Commits (`feat:`, `test:`, `chore:`, `refactor:`). One concept per commit.
- **DB path in tests:** every test uses the `temp_db` fixture — a fresh file under `tmp_path`. No shared state between tests.
- **Pydantic v2 syntax** (we're on 2.13.4): `model_config = ConfigDict(...)`, `model_validate()`, `model_dump()`.

---

## Task 0: Add test dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add pytest stack to requirements.txt**

Append these lines to `requirements.txt`:

```
pytest>=8.3.0
pytest-asyncio>=0.24.0
pytest-xdist>=3.6.0
```

- [ ] **Step 2: Install**

Run: `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`
Expected: `Successfully installed pytest-... pytest-asyncio-... pytest-xdist-...`

- [ ] **Step 3: Verify pytest discovers no tests yet**

Run: `.\.venv\Scripts\python.exe -m pytest --collect-only`
Expected: `no tests ran` (or `collected 0 items`); no errors.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add pytest + pytest-asyncio + pytest-xdist"
```

---

## Task 1: pytest configuration

**Files:**
- Create: `pyproject.toml` (modify if exists)

- [ ] **Step 1: Read current pyproject.toml**

Run: `cat pyproject.toml`
Note: if `[tool.pytest.ini_options]` already exists, modify it in-place; otherwise add.

- [ ] **Step 2: Add pytest config**

Add to `pyproject.toml` (or create the section if missing):

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-ra --strict-markers"
markers = [
    "integration: integration tests (slower)",
    "smoke: tier-3 end-to-end smoke (requires RUN_SMOKE_TESTS=1)",
]
```

- [ ] **Step 3: Verify config loads**

Run: `.\.venv\Scripts\python.exe -m pytest --co`
Expected: `collected 0 items`; no warnings about unknown markers or asyncio_mode.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: configure pytest with asyncio auto-mode and markers"
```

---

## Task 2: tests/ package + conftest skeleton

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create empty package init**

Create `tests/__init__.py` with no content (empty file).

- [ ] **Step 2: Create conftest.py with a smoke fixture**

Create `tests/conftest.py`:

```python
"""Shared fixtures for the agent_hub test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """A fresh sqlite file path under tmp_path. Not opened — tests open it themselves."""
    return tmp_path / "agent_hub.db"
```

- [ ] **Step 3: Write a sanity test that uses the fixture**

Create `tests/test_smoke.py`:

```python
def test_temp_db_path_is_fresh(temp_db_path):
    assert not temp_db_path.exists()
    assert temp_db_path.name == "agent_hub.db"
```

- [ ] **Step 4: Run it**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_smoke.py -v`
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add tests/__init__.py tests/conftest.py tests/test_smoke.py
git commit -m "test: add tests/ package and temp_db_path fixture"
```

---

## Task 3: Schema additions — tasks table

**Files:**
- Modify: `agent_hub/db.py`
- Create: `tests/test_schema.py`

- [ ] **Step 1: Write failing test for tasks table**

Create `tests/test_schema.py`:

```python
import aiosqlite
import pytest
from agent_hub.db import Database


@pytest.mark.asyncio
async def test_tasks_table_exists_after_init(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    async with aiosqlite.connect(temp_db_path) as conn:
        rows = await (await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
        )).fetchall()
    assert rows == [("tasks",)]


@pytest.mark.asyncio
async def test_tasks_table_columns(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    async with aiosqlite.connect(temp_db_path) as conn:
        rows = await (await conn.execute("PRAGMA table_info(tasks)")).fetchall()
    cols = {r[1] for r in rows}
    assert cols == {
        "id", "parent_id", "title", "description", "status", "owner",
        "worktree_path", "branch_name", "origin_chat_id",
        "created_at", "updated_at",
    }
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_schema.py -v`
Expected: both tests FAIL — the `tasks` table doesn't exist yet.

- [ ] **Step 3: Add the tasks table to db.py**

Open `agent_hub/db.py`, find the `init()` method (or wherever schema DDL lives), and add this DDL alongside the existing `messages` schema:

```python
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
    updated_at TEXT NOT NULL
);
"""
```

In `init()`, execute it:

```python
async with aiosqlite.connect(self.path) as conn:
    await conn.executescript(_SCHEMA_TASKS)
    await conn.commit()
```

(Keep the existing `messages` DDL untouched.)

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_schema.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/db.py tests/test_schema.py
git commit -m "feat(db): add tasks table"
```

---

## Task 4: Schema additions — task_events, handoff_queue, gates, worktrees

**Files:**
- Modify: `agent_hub/db.py`
- Modify: `tests/test_schema.py`

- [ ] **Step 1: Add failing tests for the four new tables**

Append to `tests/test_schema.py`:

```python
@pytest.mark.asyncio
async def test_all_new_tables_exist(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    expected = {"tasks", "task_events", "handoff_queue", "gates", "worktrees"}
    async with aiosqlite.connect(temp_db_path) as conn:
        rows = await (await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )).fetchall()
    names = {r[0] for r in rows}
    assert expected.issubset(names)


@pytest.mark.asyncio
async def test_handoff_queue_columns(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    async with aiosqlite.connect(temp_db_path) as conn:
        rows = await (await conn.execute("PRAGMA table_info(handoff_queue)")).fetchall()
    cols = {r[1] for r in rows}
    assert cols == {
        "id", "task_id", "from_agent", "to_agent", "message",
        "enqueued_at", "claimed_at",
    }


@pytest.mark.asyncio
async def test_gates_columns(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    async with aiosqlite.connect(temp_db_path) as conn:
        rows = await (await conn.execute("PRAGMA table_info(gates)")).fetchall()
    cols = {r[1] for r in rows}
    assert cols == {
        "id", "task_id", "kind", "artifact_path", "summary",
        "requested_at", "resolved_at", "resolution",
    }
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_schema.py -v`
Expected: the three new tests FAIL.

- [ ] **Step 3: Add the four new tables to db.py**

Add alongside `_SCHEMA_TASKS`:

```python
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
    resolution TEXT
);
CREATE INDEX IF NOT EXISTS idx_gates_pending ON gates(task_id, kind) WHERE resolved_at IS NULL;
"""

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
```

In `init()`, after executing `_SCHEMA_TASKS`, also execute the four new scripts:

```python
await conn.executescript(_SCHEMA_TASKS)
await conn.executescript(_SCHEMA_TASK_EVENTS)
await conn.executescript(_SCHEMA_HANDOFF_QUEUE)
await conn.executescript(_SCHEMA_GATES)
await conn.executescript(_SCHEMA_WORKTREES)
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_schema.py -v`
Expected: all schema tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/db.py tests/test_schema.py
git commit -m "feat(db): add task_events, handoff_queue, gates, worktrees tables"
```

---

## Task 5: Enable WAL mode and foreign-key enforcement

**Files:**
- Modify: `agent_hub/db.py`
- Modify: `tests/test_schema.py`

- [ ] **Step 1: Write failing tests for WAL + FK**

Append to `tests/test_schema.py`:

```python
@pytest.mark.asyncio
async def test_wal_mode_enabled(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    async with aiosqlite.connect(temp_db_path) as conn:
        row = await (await conn.execute("PRAGMA journal_mode")).fetchone()
    assert row[0].lower() == "wal"


@pytest.mark.asyncio
async def test_foreign_keys_enforced(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    async with aiosqlite.connect(temp_db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        # Try to insert a task_event for a non-existent task — should fail.
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO task_events (task_id, ts, actor, kind, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (999, "2026-05-17T00:00:00Z", "test", "comment", "{}"),
            )
            await conn.commit()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_schema.py::test_wal_mode_enabled tests/test_schema.py::test_foreign_keys_enforced -v`
Expected: both FAIL.

- [ ] **Step 3: Add pragmas to init()**

In `agent_hub/db.py`, before executing the schema scripts, set pragmas:

```python
await conn.execute("PRAGMA journal_mode = WAL")
await conn.execute("PRAGMA foreign_keys = ON")
```

WAL is persistent (survives the connection close), so this only needs to happen once.

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_schema.py -v`
Expected: all schema tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/db.py tests/test_schema.py
git commit -m "feat(db): enable WAL mode and foreign-key enforcement"
```

---

## Task 6: State machine — transition map

**Files:**
- Create: `agent_hub/state_machine.py`
- Create: `tests/test_state_machine.py`

- [ ] **Step 1: Write failing tests for the transition map**

Create `tests/test_state_machine.py`:

```python
import pytest
from agent_hub.state_machine import (
    ALLOWED_TRANSITIONS,
    TaskStatus,
    is_allowed,
    validate_transition,
    InvalidTransition,
)


def test_all_known_statuses_appear_in_map():
    statuses = {s for pair in ALLOWED_TRANSITIONS for s in pair}
    # Plus None (initial state from tasks.create).
    expected = {None, TaskStatus.PENDING, TaskStatus.PLANNING,
                TaskStatus.DESIGN_REVIEW, TaskStatus.READY,
                TaskStatus.IN_PROGRESS, TaskStatus.REVIEW,
                TaskStatus.DONE, TaskStatus.BLOCKED}
    assert expected.issubset(statuses)


def test_initial_creation_is_pending():
    assert is_allowed(None, TaskStatus.PENDING)


def test_pending_to_planning_allowed():
    assert is_allowed(TaskStatus.PENDING, TaskStatus.PLANNING)


def test_pending_to_done_disallowed():
    assert not is_allowed(TaskStatus.PENDING, TaskStatus.DONE)


def test_blocked_reachable_from_any():
    for s in (TaskStatus.PLANNING, TaskStatus.DESIGN_REVIEW,
              TaskStatus.READY, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW):
        assert is_allowed(s, TaskStatus.BLOCKED), f"BLOCKED unreachable from {s}"


def test_blocked_resumes_to_planning():
    assert is_allowed(TaskStatus.BLOCKED, TaskStatus.PLANNING)


def test_review_kickback_to_in_progress():
    assert is_allowed(TaskStatus.REVIEW, TaskStatus.IN_PROGRESS)


def test_design_reject_returns_to_planning():
    assert is_allowed(TaskStatus.DESIGN_REVIEW, TaskStatus.PLANNING)


def test_validate_raises_on_invalid():
    with pytest.raises(InvalidTransition) as exc:
        validate_transition(TaskStatus.PENDING, TaskStatus.DONE)
    assert "PENDING" in str(exc.value)
    assert "DONE" in str(exc.value)


def test_validate_returns_none_on_valid():
    assert validate_transition(TaskStatus.READY, TaskStatus.IN_PROGRESS) is None
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_state_machine.py -v`
Expected: import error or every test FAILS (module doesn't exist).

- [ ] **Step 3: Create state_machine.py**

Create `agent_hub/state_machine.py`:

```python
"""Task status enum, allowed-transition map, and validator.

The map is data — every transition the system performs MUST pass through
validate_transition(). Tests cover both allowed and disallowed cases
exhaustively.
"""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    DESIGN_REVIEW = "design_review"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"


# Allowed transitions as (from, to) pairs. `None` means "no prior status"
# (i.e. the initial create).
ALLOWED_TRANSITIONS: frozenset[tuple[TaskStatus | None, TaskStatus]] = frozenset({
    (None, TaskStatus.PENDING),
    (TaskStatus.PENDING, TaskStatus.PLANNING),
    (TaskStatus.PLANNING, TaskStatus.DESIGN_REVIEW),
    (TaskStatus.PLANNING, TaskStatus.IN_PROGRESS),  # small tasks that skip the architect
    (TaskStatus.DESIGN_REVIEW, TaskStatus.READY),   # /approve
    (TaskStatus.DESIGN_REVIEW, TaskStatus.PLANNING),  # /reject — back to planning
    (TaskStatus.READY, TaskStatus.IN_PROGRESS),
    (TaskStatus.IN_PROGRESS, TaskStatus.REVIEW),
    (TaskStatus.REVIEW, TaskStatus.DONE),
    (TaskStatus.REVIEW, TaskStatus.IN_PROGRESS),    # reviewer kick-back
    # Any → blocked (enumerated)
    (TaskStatus.PLANNING, TaskStatus.BLOCKED),
    (TaskStatus.DESIGN_REVIEW, TaskStatus.BLOCKED),
    (TaskStatus.READY, TaskStatus.BLOCKED),
    (TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED),
    (TaskStatus.REVIEW, TaskStatus.BLOCKED),
    # Resume from blocked routes to PM via planning
    (TaskStatus.BLOCKED, TaskStatus.PLANNING),
})


class InvalidTransition(ValueError):
    """Raised when a status transition is not in ALLOWED_TRANSITIONS."""


def is_allowed(from_status: TaskStatus | None, to_status: TaskStatus) -> bool:
    return (from_status, to_status) in ALLOWED_TRANSITIONS


def validate_transition(from_status: TaskStatus | None, to_status: TaskStatus) -> None:
    if not is_allowed(from_status, to_status):
        from_label = from_status.name if from_status else "NONE"
        raise InvalidTransition(
            f"Invalid status transition: {from_label} -> {to_status.name}"
        )
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_state_machine.py -v`
Expected: all tests PASS (10 of them).

- [ ] **Step 5: Commit**

```bash
git add agent_hub/state_machine.py tests/test_state_machine.py
git commit -m "feat(state-machine): add TaskStatus enum and transition validator"
```

---

## Task 7: Exhaustive transition coverage test

**Files:**
- Modify: `tests/test_state_machine.py`

- [ ] **Step 1: Add a parameterized cross-product test**

Append to `tests/test_state_machine.py`:

```python
import itertools


def test_every_pair_in_map_passes_validation():
    """Every (from, to) explicitly in ALLOWED_TRANSITIONS must pass validation."""
    for from_s, to_s in ALLOWED_TRANSITIONS:
        validate_transition(from_s, to_s)  # must not raise


def test_every_pair_not_in_map_fails_validation():
    """Cross-product minus allowed set must all raise InvalidTransition."""
    all_statuses = list(TaskStatus) + [None]
    for from_s, to_s in itertools.product(all_statuses, TaskStatus):
        if (from_s, to_s) in ALLOWED_TRANSITIONS:
            continue
        with pytest.raises(InvalidTransition):
            validate_transition(from_s, to_s)
```

- [ ] **Step 2: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_state_machine.py -v`
Expected: all PASS, including the new two.

- [ ] **Step 3: Commit**

```bash
git add tests/test_state_machine.py
git commit -m "test(state-machine): exhaustive cross-product coverage"
```

---

## Task 8: Task pydantic models

**Files:**
- Create: `agent_hub/tasks/__init__.py`
- Create: `agent_hub/tasks/models.py`
- Create: `tests/test_tasks_models.py`

- [ ] **Step 1: Create empty package init**

Create `agent_hub/tasks/__init__.py` (empty).

- [ ] **Step 2: Write failing tests for models**

Create `tests/test_tasks_models.py`:

```python
from datetime import datetime

import pytest
from pydantic import ValidationError

from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.models import Task, TaskEvent, Gate, HandoffRow


def test_task_minimal_fields():
    t = Task(
        id=1, title="x", description="y",
        status=TaskStatus.PENDING, origin_chat_id=42,
        created_at=datetime(2026, 5, 17), updated_at=datetime(2026, 5, 17),
    )
    assert t.status == TaskStatus.PENDING
    assert t.parent_id is None
    assert t.owner is None


def test_task_rejects_unknown_status():
    with pytest.raises(ValidationError):
        Task(
            id=1, title="x", description="y",
            status="bogus", origin_chat_id=42,
            created_at=datetime(2026, 5, 17), updated_at=datetime(2026, 5, 17),
        )


def test_task_event_kind_required():
    ev = TaskEvent(
        id=1, task_id=1, ts=datetime(2026, 5, 17),
        actor="pm", kind="comment", payload={"body": "hi"},
    )
    assert ev.payload == {"body": "hi"}


def test_gate_resolution_optional():
    g = Gate(
        id=1, task_id=1, kind="design",
        requested_at=datetime(2026, 5, 17),
    )
    assert g.resolution is None
    assert g.resolved_at is None


def test_handoff_row_basic():
    h = HandoffRow(
        id=1, task_id=1, from_agent="pm", to_agent="architect",
        message="hi", enqueued_at=datetime(2026, 5, 17),
    )
    assert h.claimed_at is None
```

- [ ] **Step 3: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tasks_models.py -v`
Expected: import error (module doesn't exist).

- [ ] **Step 4: Implement models**

Create `agent_hub/tasks/models.py`:

```python
"""Pydantic models for task rows and event payloads.

These mirror the SQLite schema in agent_hub.db. Repositories convert
between rows (tuples) and these models at the boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent_hub.state_machine import TaskStatus


class Task(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: int
    parent_id: int | None = None
    title: str
    description: str
    status: TaskStatus
    owner: str | None = None
    worktree_path: str | None = None
    branch_name: str | None = None
    origin_chat_id: int
    created_at: datetime
    updated_at: datetime


class TaskEvent(BaseModel):
    id: int
    task_id: int
    ts: datetime
    actor: str
    kind: str  # comment | status_change | handoff | gate_request | gate_resolve | push | error
    payload: dict[str, Any]


class Gate(BaseModel):
    id: int
    task_id: int
    kind: str  # "design" in v1
    artifact_path: str | None = None
    summary: str | None = None
    requested_at: datetime
    resolved_at: datetime | None = None
    resolution: str | None = None  # approved | rejected | None


class HandoffRow(BaseModel):
    id: int
    task_id: int
    from_agent: str
    to_agent: str
    message: str
    enqueued_at: datetime
    claimed_at: datetime | None = None
```

- [ ] **Step 5: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tasks_models.py -v`
Expected: all 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/tasks/__init__.py agent_hub/tasks/models.py tests/test_tasks_models.py
git commit -m "feat(tasks): add Task, TaskEvent, Gate, HandoffRow pydantic models"
```

---

## Task 9: Task repository — create + get

**Files:**
- Create: `agent_hub/tasks/repository.py`
- Create: `tests/test_tasks_repository.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tasks_repository.py`:

```python
import pytest
from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def repo(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path)


@pytest.mark.asyncio
async def test_create_returns_task_with_id(repo):
    t = await repo.create(
        title="add /health endpoint",
        description="ping D1 and return OK",
        origin_chat_id=12345,
    )
    assert t.id > 0
    assert t.title == "add /health endpoint"
    assert t.status == TaskStatus.PENDING
    assert t.parent_id is None
    assert t.owner is None


@pytest.mark.asyncio
async def test_get_returns_created_task(repo):
    created = await repo.create(title="x", description="y", origin_chat_id=1)
    fetched = await repo.get(created.id)
    assert fetched.id == created.id
    assert fetched.title == "x"


@pytest.mark.asyncio
async def test_get_unknown_returns_none(repo):
    assert await repo.get(99999) is None


@pytest.mark.asyncio
async def test_create_with_parent(repo):
    parent = await repo.create(title="epic", description="...", origin_chat_id=1)
    child = await repo.create(
        title="leaf", description="...", origin_chat_id=1, parent_id=parent.id,
    )
    assert child.parent_id == parent.id
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tasks_repository.py -v`
Expected: import error.

- [ ] **Step 3: Implement repository.create + get**

Create `agent_hub/tasks/repository.py`:

```python
"""Async SQLite repository for tasks, events, gates, and handoffs.

Each method opens its own connection (cheap on SQLite). Foreign keys
are enforced; transitions are validated via state_machine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.models import Task


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _row_to_task(row: aiosqlite.Row) -> Task:
    return Task(
        id=row[0],
        parent_id=row[1],
        title=row[2],
        description=row[3],
        status=TaskStatus(row[4]),
        owner=row[5],
        worktree_path=row[6],
        branch_name=row[7],
        origin_chat_id=row[8],
        created_at=_parse_dt(row[9]),
        updated_at=_parse_dt(row[10]),
    )


_TASK_COLS = (
    "id, parent_id, title, description, status, owner, "
    "worktree_path, branch_name, origin_chat_id, created_at, updated_at"
)


class TaskRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def _connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def create(
        self,
        *,
        title: str,
        description: str,
        origin_chat_id: int,
        parent_id: int | None = None,
        owner: str | None = None,
    ) -> Task:
        now = _utcnow_iso()
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO tasks "
                "(parent_id, title, description, status, owner, origin_chat_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (parent_id, title, description, TaskStatus.PENDING.value,
                 owner, origin_chat_id, now, now),
            )
            await conn.commit()
            task_id = cur.lastrowid
        return await self.get(task_id)  # type: ignore[return-value]

    async def get(self, task_id: int) -> Task | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_TASK_COLS} FROM tasks WHERE id = ?", (task_id,),
            )
            row = await cur.fetchone()
        return _row_to_task(row) if row else None
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tasks_repository.py -v`
Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/tasks/repository.py tests/test_tasks_repository.py
git commit -m "feat(tasks): add TaskRepository.create and TaskRepository.get"
```

---

## Task 10: Task repository — list + tree

**Files:**
- Modify: `agent_hub/tasks/repository.py`
- Modify: `tests/test_tasks_repository.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_tasks_repository.py`:

```python
@pytest.mark.asyncio
async def test_list_filters_by_status(repo):
    a = await repo.create(title="a", description="-", origin_chat_id=1)
    b = await repo.create(title="b", description="-", origin_chat_id=1)
    # Both are PENDING after creation.
    pending = await repo.list(status=TaskStatus.PENDING)
    assert {t.id for t in pending} == {a.id, b.id}

    none = await repo.list(status=TaskStatus.DONE)
    assert none == []


@pytest.mark.asyncio
async def test_list_filters_by_owner(repo):
    a = await repo.create(title="a", description="-", origin_chat_id=1, owner="pm")
    b = await repo.create(title="b", description="-", origin_chat_id=1, owner="architect")
    pm_tasks = await repo.list(owner="pm")
    assert [t.id for t in pm_tasks] == [a.id]


@pytest.mark.asyncio
async def test_tree_returns_root_with_descendants(repo):
    epic = await repo.create(title="epic", description="-", origin_chat_id=1)
    leaf1 = await repo.create(title="l1", description="-", origin_chat_id=1, parent_id=epic.id)
    leaf2 = await repo.create(title="l2", description="-", origin_chat_id=1, parent_id=epic.id)
    grand = await repo.create(title="gl", description="-", origin_chat_id=1, parent_id=leaf1.id)

    tree = await repo.tree(epic.id)
    assert tree["root"].id == epic.id
    descendant_ids = {t.id for t in tree["descendants"]}
    assert descendant_ids == {leaf1.id, leaf2.id, grand.id}


@pytest.mark.asyncio
async def test_tree_unknown_returns_none(repo):
    assert await repo.tree(99999) is None
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tasks_repository.py -v`
Expected: the four new tests FAIL.

- [ ] **Step 3: Implement list and tree**

Append to `agent_hub/tasks/repository.py`:

```python
    async def list(
        self,
        *,
        status: TaskStatus | None = None,
        owner: str | None = None,
        parent_id: int | None = None,
    ) -> list[Task]:
        clauses: list[str] = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if owner is not None:
            clauses.append("owner = ?")
            params.append(owner)
        if parent_id is not None:
            clauses.append("parent_id = ?")
            params.append(parent_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT {_TASK_COLS} FROM tasks {where} ORDER BY id ASC"
        async with await self._connect() as conn:
            cur = await conn.execute(sql, tuple(params))
            rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]

    async def tree(self, root_id: int) -> dict | None:
        """Returns {'root': Task, 'descendants': list[Task]} or None if root_id is unknown."""
        root = await self.get(root_id)
        if root is None:
            return None
        descendants: list[Task] = []
        frontier = [root.id]
        async with await self._connect() as conn:
            while frontier:
                placeholders = ",".join("?" * len(frontier))
                cur = await conn.execute(
                    f"SELECT {_TASK_COLS} FROM tasks WHERE parent_id IN ({placeholders})",
                    tuple(frontier),
                )
                rows = await cur.fetchall()
                children = [_row_to_task(r) for r in rows]
                descendants.extend(children)
                frontier = [c.id for c in children]
        return {"root": root, "descendants": descendants}
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tasks_repository.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/tasks/repository.py tests/test_tasks_repository.py
git commit -m "feat(tasks): add TaskRepository.list and TaskRepository.tree"
```

---

## Task 11: Task repository — update (with transition validation)

**Files:**
- Modify: `agent_hub/tasks/repository.py`
- Modify: `tests/test_tasks_repository.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_tasks_repository.py`:

```python
from agent_hub.state_machine import InvalidTransition


@pytest.mark.asyncio
async def test_update_status_valid_transition(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    updated = await repo.update(t.id, status=TaskStatus.PLANNING)
    assert updated.status == TaskStatus.PLANNING


@pytest.mark.asyncio
async def test_update_status_invalid_transition_raises(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    with pytest.raises(InvalidTransition):
        await repo.update(t.id, status=TaskStatus.DONE)


@pytest.mark.asyncio
async def test_update_owner(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    updated = await repo.update(t.id, owner="pm")
    assert updated.owner == "pm"


@pytest.mark.asyncio
async def test_update_unknown_task_returns_none(repo):
    assert await repo.update(99999, owner="x") is None


@pytest.mark.asyncio
async def test_update_refreshes_updated_at(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    original = t.updated_at
    import asyncio
    await asyncio.sleep(0.01)
    updated = await repo.update(t.id, owner="pm")
    assert updated.updated_at > original
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tasks_repository.py -v`
Expected: the five new tests FAIL.

- [ ] **Step 3: Implement update**

Append to `agent_hub/tasks/repository.py`:

```python
    async def update(
        self,
        task_id: int,
        *,
        status: TaskStatus | None = None,
        owner: str | None = None,
        worktree_path: str | None = None,
        branch_name: str | None = None,
    ) -> Task | None:
        from agent_hub.state_machine import validate_transition

        current = await self.get(task_id)
        if current is None:
            return None
        if status is not None and status != current.status:
            validate_transition(current.status, status)

        sets: list[str] = ["updated_at = ?"]
        params: list = [_utcnow_iso()]
        if status is not None:
            sets.append("status = ?")
            params.append(status.value)
        if owner is not None:
            sets.append("owner = ?")
            params.append(owner)
        if worktree_path is not None:
            sets.append("worktree_path = ?")
            params.append(worktree_path)
        if branch_name is not None:
            sets.append("branch_name = ?")
            params.append(branch_name)
        params.append(task_id)

        async with await self._connect() as conn:
            await conn.execute(
                f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
            await conn.commit()
        return await self.get(task_id)
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tasks_repository.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/tasks/repository.py tests/test_tasks_repository.py
git commit -m "feat(tasks): add TaskRepository.update with transition validation"
```

---

## Task 12: Task repository — comment + event listing

**Files:**
- Modify: `agent_hub/tasks/repository.py`
- Modify: `tests/test_tasks_repository.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_tasks_repository.py`:

```python
@pytest.mark.asyncio
async def test_comment_appends_event(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    event_id = await repo.comment(t.id, actor="pm", body="filed the task")
    assert event_id > 0

    events = await repo.events(t.id)
    assert len(events) == 1
    assert events[0].kind == "comment"
    assert events[0].actor == "pm"
    assert events[0].payload == {"body": "filed the task"}


@pytest.mark.asyncio
async def test_events_ordered_by_time(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    import asyncio
    await repo.comment(t.id, actor="pm", body="one")
    await asyncio.sleep(0.01)
    await repo.comment(t.id, actor="architect", body="two")
    events = await repo.events(t.id)
    assert [e.payload["body"] for e in events] == ["one", "two"]


@pytest.mark.asyncio
async def test_events_limit_returns_recent(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    for i in range(5):
        await repo.comment(t.id, actor="pm", body=str(i))
    events = await repo.events(t.id, limit=2)
    assert [e.payload["body"] for e in events] == ["3", "4"]


@pytest.mark.asyncio
async def test_status_change_writes_event(repo):
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(t.id, status=TaskStatus.PLANNING)
    events = await repo.events(t.id)
    kinds = [e.kind for e in events]
    assert "status_change" in kinds
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tasks_repository.py -v`
Expected: the four new tests FAIL.

- [ ] **Step 3: Implement comment + events; auto-event on update**

Append to `agent_hub/tasks/repository.py`:

```python
    async def comment(self, task_id: int, *, actor: str, body: str) -> int:
        import json
        payload = json.dumps({"body": body})
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO task_events (task_id, ts, actor, kind, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, _utcnow_iso(), actor, "comment", payload),
            )
            await conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    async def events(self, task_id: int, *, limit: int | None = None) -> list:
        """Returns TaskEvent list ordered by ts ASC. If limit, returns the *most recent* `limit`."""
        import json
        from agent_hub.tasks.models import TaskEvent

        if limit is not None:
            sql = (
                "SELECT id, task_id, ts, actor, kind, payload_json FROM task_events "
                "WHERE task_id = ? ORDER BY ts DESC LIMIT ?"
            )
            args = (task_id, limit)
        else:
            sql = (
                "SELECT id, task_id, ts, actor, kind, payload_json FROM task_events "
                "WHERE task_id = ? ORDER BY ts ASC"
            )
            args = (task_id,)
        async with await self._connect() as conn:
            cur = await conn.execute(sql, args)
            rows = await cur.fetchall()
        events = [
            TaskEvent(
                id=r[0], task_id=r[1], ts=_parse_dt(r[2]),
                actor=r[3], kind=r[4], payload=json.loads(r[5]),
            )
            for r in rows
        ]
        if limit is not None:
            events.reverse()  # restore chronological order
        return events
```

Now modify `update()` to also write a `status_change` event when status changes. Replace the body of `update()` after the `validate_transition` line and before the `sets: list[str] = ...` line, by capturing whether status is changing:

```python
        status_changed = status is not None and status != current.status
```

Then after `await conn.commit()` and BEFORE the final `return await self.get(task_id)`, insert:

```python
        if status_changed:
            await self.comment(task_id, actor="system", body=f"status: {current.status.value} -> {status.value}")
            # Override kind from "comment" to "status_change" — small follow-up:
            # we'll record this directly to keep semantics clean.
```

Actually, replace the `comment(..., body=...)` trick with a direct event insert. Add a private helper:

```python
    async def _append_event(self, task_id: int, *, actor: str, kind: str, payload: dict) -> int:
        import json
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO task_events (task_id, ts, actor, kind, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, _utcnow_iso(), actor, kind, json.dumps(payload)),
            )
            await conn.commit()
            return cur.lastrowid  # type: ignore[return-value]
```

Then rewrite `comment` to use it:

```python
    async def comment(self, task_id: int, *, actor: str, body: str) -> int:
        return await self._append_event(task_id, actor=actor, kind="comment", payload={"body": body})
```

And in `update`, after the UPDATE commit, append a status_change event when applicable:

```python
        if status_changed:
            await self._append_event(
                task_id,
                actor="system",
                kind="status_change",
                payload={"from": current.status.value, "to": status.value},
            )
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_tasks_repository.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/tasks/repository.py tests/test_tasks_repository.py
git commit -m "feat(tasks): add comment/events APIs and auto status_change event"
```

---

## Task 13: Handoff queue — enqueue

**Files:**
- Create: `agent_hub/tasks/handoff_queue.py`
- Create: `tests/test_handoff_queue.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_handoff_queue.py`:

```python
import pytest

from agent_hub.db import Database
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), HandoffQueue(temp_db_path)


@pytest.mark.asyncio
async def test_enqueue_returns_id(deps):
    repo, queue = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    qid = await queue.enqueue(
        task_id=t.id, from_agent="pm", to_agent="architect", message="design this",
    )
    assert qid > 0


@pytest.mark.asyncio
async def test_pending_returns_unclaimed(deps):
    repo, queue = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await queue.enqueue(task_id=t.id, from_agent="pm", to_agent="architect", message="m1")
    await queue.enqueue(task_id=t.id, from_agent="pm", to_agent="reviewer", message="m2")
    pending = await queue.pending()
    assert len(pending) == 2
    assert pending[0].message == "m1"
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_handoff_queue.py -v`
Expected: import error.

- [ ] **Step 3: Implement enqueue + pending**

Create `agent_hub/tasks/handoff_queue.py`:

```python
"""Handoff queue — agent-to-agent dispatch messages.

Producers (agents via MCP) call enqueue().
Consumers (the orchestrator handoff loop, landing in a later plan)
call claim() atomically to pop a row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from agent_hub.tasks.models import HandoffRow


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


_COLS = "id, task_id, from_agent, to_agent, message, enqueued_at, claimed_at"


def _row_to_model(row) -> HandoffRow:
    return HandoffRow(
        id=row[0], task_id=row[1], from_agent=row[2], to_agent=row[3],
        message=row[4], enqueued_at=_parse_dt(row[5]),
        claimed_at=_parse_dt(row[6]) if row[6] else None,
    )


class HandoffQueue:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def _connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def enqueue(
        self, *, task_id: int, from_agent: str, to_agent: str, message: str,
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO handoff_queue "
                "(task_id, from_agent, to_agent, message, enqueued_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, from_agent, to_agent, message, _utcnow_iso()),
            )
            await conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    async def pending(self) -> list[HandoffRow]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM handoff_queue WHERE claimed_at IS NULL "
                "ORDER BY enqueued_at ASC"
            )
            rows = await cur.fetchall()
        return [_row_to_model(r) for r in rows]
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_handoff_queue.py -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/tasks/handoff_queue.py tests/test_handoff_queue.py
git commit -m "feat(handoff): add enqueue and pending listing"
```

---

## Task 14: Handoff queue — atomic claim with race test

**Files:**
- Modify: `agent_hub/tasks/handoff_queue.py`
- Modify: `tests/test_handoff_queue.py`

- [ ] **Step 1: Add failing tests including the race**

Append to `tests/test_handoff_queue.py`:

```python
import asyncio


@pytest.mark.asyncio
async def test_claim_returns_one_unclaimed(deps):
    repo, queue = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    qid = await queue.enqueue(task_id=t.id, from_agent="pm", to_agent="x", message="m")

    claimed = await queue.claim()
    assert claimed is not None
    assert claimed.id == qid
    assert claimed.claimed_at is not None


@pytest.mark.asyncio
async def test_claim_returns_none_when_empty(deps):
    repo, queue = deps
    assert await queue.claim() is None


@pytest.mark.asyncio
async def test_claim_skips_already_claimed(deps):
    repo, queue = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await queue.enqueue(task_id=t.id, from_agent="pm", to_agent="x", message="m")
    first = await queue.claim()
    assert first is not None
    second = await queue.claim()
    assert second is None


@pytest.mark.asyncio
async def test_concurrent_claim_one_winner(deps):
    """10 concurrent claim() calls against 1 row — exactly one wins, others get None."""
    repo, queue = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await queue.enqueue(task_id=t.id, from_agent="pm", to_agent="x", message="m")

    results = await asyncio.gather(*[queue.claim() for _ in range(10)])
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_handoff_queue.py -v`
Expected: the four new tests FAIL — `claim()` doesn't exist.

- [ ] **Step 3: Implement atomic claim**

Append to `agent_hub/tasks/handoff_queue.py`:

```python
    async def claim(self) -> HandoffRow | None:
        """Atomically claim the oldest unclaimed row, or None if queue empty.

        Uses an UPDATE...RETURNING with a sub-select to pick the row.
        Wrapped in BEGIN IMMEDIATE so two callers race on a write lock
        rather than both reading the same row.
        """
        now = _utcnow_iso()
        async with await self._connect() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                cur = await conn.execute(
                    "SELECT id FROM handoff_queue "
                    "WHERE claimed_at IS NULL "
                    "ORDER BY enqueued_at ASC LIMIT 1"
                )
                row = await cur.fetchone()
                if row is None:
                    await conn.execute("ROLLBACK")
                    return None
                row_id = row[0]
                await conn.execute(
                    "UPDATE handoff_queue SET claimed_at = ? "
                    "WHERE id = ? AND claimed_at IS NULL",
                    (now, row_id),
                )
                cur = await conn.execute(
                    f"SELECT {_COLS} FROM handoff_queue WHERE id = ?", (row_id,),
                )
                fetched = await cur.fetchone()
                await conn.commit()
            except Exception:
                await conn.execute("ROLLBACK")
                raise
        return _row_to_model(fetched) if fetched else None
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_handoff_queue.py -v`
Expected: all PASS, including the 10-coroutine race.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/tasks/handoff_queue.py tests/test_handoff_queue.py
git commit -m "feat(handoff): add atomic claim with race-test coverage"
```

---

## Task 15: Gates module

**Files:**
- Create: `agent_hub/tasks/gates.py`
- Create: `tests/test_gates.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_gates.py`:

```python
import pytest

from agent_hub.db import Database
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), GateRepository(temp_db_path)


@pytest.mark.asyncio
async def test_request_creates_pending_gate(deps):
    repo, gates = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    gid = await gates.request(task_id=t.id, kind="design", summary="please review")
    assert gid > 0
    g = await gates.get(gid)
    assert g.task_id == t.id
    assert g.kind == "design"
    assert g.summary == "please review"
    assert g.resolution is None
    assert g.resolved_at is None


@pytest.mark.asyncio
async def test_status_pending_then_resolved(deps):
    repo, gates = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await gates.request(task_id=t.id, kind="design")
    assert await gates.status(task_id=t.id, kind="design") == "pending"

    await gates.resolve(task_id=t.id, kind="design", resolution="approved")
    assert await gates.status(task_id=t.id, kind="design") == "approved"


@pytest.mark.asyncio
async def test_status_none_when_no_gate(deps):
    repo, gates = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    assert await gates.status(task_id=t.id, kind="design") == "none"


@pytest.mark.asyncio
async def test_resolve_unknown_raises(deps):
    repo, gates = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    with pytest.raises(ValueError):
        await gates.resolve(task_id=t.id, kind="design", resolution="approved")


@pytest.mark.asyncio
async def test_resolve_already_resolved_is_noop(deps):
    repo, gates = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await gates.request(task_id=t.id, kind="design")
    await gates.resolve(task_id=t.id, kind="design", resolution="approved")
    # Second resolve should not raise and should not flip the resolution.
    await gates.resolve(task_id=t.id, kind="design", resolution="rejected")
    assert await gates.status(task_id=t.id, kind="design") == "approved"
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_gates.py -v`
Expected: import error.

- [ ] **Step 3: Implement GateRepository**

Create `agent_hub/tasks/gates.py`:

```python
"""Human-in-the-loop gates (design approval).

In v1 the only `kind` is "design". When the architect calls
gate.request(...), the task pauses on `design_review` status; the
orchestrator (later plan) detects the pending row and DMs the user.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from agent_hub.tasks.models import Gate


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


_COLS = "id, task_id, kind, artifact_path, summary, requested_at, resolved_at, resolution"


def _row_to_gate(row) -> Gate:
    return Gate(
        id=row[0], task_id=row[1], kind=row[2],
        artifact_path=row[3], summary=row[4],
        requested_at=_parse_dt(row[5]),
        resolved_at=_parse_dt(row[6]) if row[6] else None,
        resolution=row[7],
    )


class GateRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def _connect(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def request(
        self, *, task_id: int, kind: str,
        artifact_path: str | None = None, summary: str | None = None,
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "INSERT INTO gates (task_id, kind, artifact_path, summary, requested_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, kind, artifact_path, summary, _utcnow_iso()),
            )
            await conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    async def get(self, gate_id: int) -> Gate | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM gates WHERE id = ?", (gate_id,),
            )
            row = await cur.fetchone()
        return _row_to_gate(row) if row else None

    async def status(self, *, task_id: int, kind: str) -> str:
        """Returns 'pending' | 'approved' | 'rejected' | 'none'."""
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT resolution, resolved_at FROM gates "
                "WHERE task_id = ? AND kind = ? "
                "ORDER BY requested_at DESC LIMIT 1",
                (task_id, kind),
            )
            row = await cur.fetchone()
        if row is None:
            return "none"
        if row[1] is None:
            return "pending"
        return row[0] or "pending"

    async def resolve(self, *, task_id: int, kind: str, resolution: str) -> None:
        """Resolve the latest pending gate for (task_id, kind). Idempotent."""
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, resolved_at FROM gates "
                "WHERE task_id = ? AND kind = ? "
                "ORDER BY requested_at DESC LIMIT 1",
                (task_id, kind),
            )
            row = await cur.fetchone()
            if row is None:
                raise ValueError(f"No gate exists for task={task_id} kind={kind!r}")
            if row[1] is not None:
                # Already resolved — no-op (idempotent).
                return
            await conn.execute(
                "UPDATE gates SET resolved_at = ?, resolution = ? WHERE id = ?",
                (_utcnow_iso(), resolution, row[0]),
            )
            await conn.commit()
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_gates.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/tasks/gates.py tests/test_gates.py
git commit -m "feat(gates): add GateRepository with request/resolve/status"
```

---

## Task 16: MCP server package skeleton

**Files:**
- Create: `agent_hub/mcp_server/__init__.py`
- Create: `agent_hub/mcp_server/__main__.py`
- Create: `agent_hub/mcp_server/server.py`
- Create: `agent_hub/mcp_server/tools/__init__.py`

- [ ] **Step 1: Create empty package files**

Create `agent_hub/mcp_server/__init__.py` (empty).
Create `agent_hub/mcp_server/tools/__init__.py` (empty).

- [ ] **Step 2: Create server.py with FastMCP setup**

Create `agent_hub/mcp_server/server.py`:

```python
"""Agent Hub MCP server — stdio transport, exposes orchestration tools.

Launched per-agent by the ClaudeSDKClient via:
  ClaudeAgentOptions(mcp_servers=[{"command": "python", "args": ["-m", "agent_hub.mcp_server"]}])

The server is stateless beyond the SQLite database it reads/writes.
DB path is resolved from the AGENT_HUB_DB env var (set by the host
process before launching the SDK client).
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP


def _resolve_db_path() -> Path:
    raw = os.environ.get("AGENT_HUB_DB")
    if not raw:
        raise RuntimeError(
            "AGENT_HUB_DB env var must be set when launching agent_hub.mcp_server"
        )
    return Path(raw)


def build_server() -> FastMCP:
    """Construct the FastMCP server with all tools registered.

    Kept as a function so tests can build a fresh server per case.
    """
    server = FastMCP("agent-hub")
    db_path = _resolve_db_path()

    # Tool registration happens in submodules so each tool family is
    # independently testable. Each register_X function takes the server
    # plus the db_path and binds tools that close over a repository.
    from agent_hub.mcp_server.tools.tasks_tools import register as register_tasks
    from agent_hub.mcp_server.tools.handoff_tool import register as register_handoff
    from agent_hub.mcp_server.tools.gate_tools import register as register_gate

    register_tasks(server, db_path)
    register_handoff(server, db_path)
    register_gate(server, db_path)
    return server
```

- [ ] **Step 3: Create the entrypoint**

Create `agent_hub/mcp_server/__main__.py`:

```python
"""Entrypoint: `python -m agent_hub.mcp_server`."""

from agent_hub.mcp_server.server import build_server


def main() -> None:
    server = build_server()
    server.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Sanity test — module imports without launching**

Create `tests/test_mcp_server_import.py`:

```python
def test_module_imports():
    # build_server() requires AGENT_HUB_DB; just check importability.
    import agent_hub.mcp_server.server  # noqa: F401
    import agent_hub.mcp_server.__main__  # noqa: F401
```

Also create placeholder tool modules so `build_server` can import them (we'll fill them in next tasks):

`agent_hub/mcp_server/tools/tasks_tools.py`:

```python
"""Tasks MCP tools — populated in Task 17."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP


def register(server: FastMCP, db_path: Path) -> None:
    """No tools registered yet — implemented in Task 17."""
    return
```

`agent_hub/mcp_server/tools/handoff_tool.py`:

```python
"""Handoff MCP tool — populated in Task 18."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP


def register(server: FastMCP, db_path: Path) -> None:
    return
```

`agent_hub/mcp_server/tools/gate_tools.py`:

```python
"""Gate MCP tools — populated in Task 19."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP


def register(server: FastMCP, db_path: Path) -> None:
    return
```

- [ ] **Step 5: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_mcp_server_import.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/mcp_server tests/test_mcp_server_import.py
git commit -m "feat(mcp): add server skeleton with tool-registration stubs"
```

---

## Task 17: MCP tools — tasks.*

**Files:**
- Modify: `agent_hub/mcp_server/tools/tasks_tools.py`
- Create: `tests/test_mcp_tools_tasks.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_mcp_tools_tasks.py`:

```python
"""Direct-call tests for the tasks.* tool functions.

We don't spin up the MCP server here — we call the underlying handler
functions registered on a FastMCP instance and assert on the DB state.
"""

import pytest
from mcp.server.fastmcp import FastMCP

from agent_hub.db import Database
from agent_hub.mcp_server.tools.tasks_tools import register
from agent_hub.state_machine import TaskStatus


@pytest.fixture
async def server_and_db(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    server = FastMCP("test")
    register(server, temp_db_path)
    return server, temp_db_path


def _tool(server: FastMCP, name: str):
    """Look up a registered tool's underlying async function."""
    tools = server._tool_manager.list_tools()  # type: ignore[attr-defined]
    for t in tools:
        if t.name == name:
            return server._tool_manager.get_tool(name).fn  # type: ignore[attr-defined]
    raise KeyError(f"tool {name!r} not registered")


@pytest.mark.asyncio
async def test_tasks_create(server_and_db):
    server, _ = server_and_db
    fn = _tool(server, "tasks.create")
    result = await fn(title="x", description="y", origin_chat_id=1)
    assert result["id"] > 0
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_tasks_get(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    get = _tool(server, "tasks.get")
    created = await create(title="x", description="y", origin_chat_id=1)
    got = await get(task_id=created["id"])
    assert got["task"]["id"] == created["id"]
    assert got["recent_events"] == []


@pytest.mark.asyncio
async def test_tasks_list_filters(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    lst = _tool(server, "tasks.list")
    await create(title="a", description="-", origin_chat_id=1)
    await create(title="b", description="-", origin_chat_id=1)
    pending = await lst(status="pending")
    assert len(pending) == 2


@pytest.mark.asyncio
async def test_tasks_update_status(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    update = _tool(server, "tasks.update")
    t = await create(title="x", description="-", origin_chat_id=1)
    result = await update(task_id=t["id"], status="planning")
    assert result["status"] == "planning"


@pytest.mark.asyncio
async def test_tasks_update_invalid_status_returns_error(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    update = _tool(server, "tasks.update")
    t = await create(title="x", description="-", origin_chat_id=1)
    result = await update(task_id=t["id"], status="done")  # pending->done is invalid
    assert "error" in result
    assert "Invalid" in result["error"] or "transition" in result["error"].lower()


@pytest.mark.asyncio
async def test_tasks_comment(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    comment = _tool(server, "tasks.comment")
    get = _tool(server, "tasks.get")
    t = await create(title="x", description="-", origin_chat_id=1)
    event_id = await comment(task_id=t["id"], body="filed it")
    assert event_id["event_id"] > 0
    detail = await get(task_id=t["id"])
    assert detail["recent_events"][-1]["payload"]["body"] == "filed it"


@pytest.mark.asyncio
async def test_tasks_tree(server_and_db):
    server, _ = server_and_db
    create = _tool(server, "tasks.create")
    tree = _tool(server, "tasks.tree")
    epic = await create(title="epic", description="-", origin_chat_id=1)
    leaf = await create(title="leaf", description="-", origin_chat_id=1, parent_id=epic["id"])
    result = await tree(task_id=epic["id"])
    assert result["root"]["id"] == epic["id"]
    assert [d["id"] for d in result["descendants"]] == [leaf["id"]]
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_mcp_tools_tasks.py -v`
Expected: tool lookups fail (no tools registered yet).

- [ ] **Step 3: Implement the tasks.* tools**

Replace contents of `agent_hub/mcp_server/tools/tasks_tools.py`:

```python
"""Tasks MCP tools — thin wrappers around TaskRepository.

Each tool's input is validated by FastMCP from its type annotations.
Errors from the repository (e.g. InvalidTransition) are caught and
returned as {"error": str} so the calling agent can self-correct on
the next turn.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.repository import TaskRepository


def _task_to_dict(task) -> dict:
    return {
        "id": task.id,
        "parent_id": task.parent_id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value,
        "owner": task.owner,
        "worktree_path": task.worktree_path,
        "branch_name": task.branch_name,
        "origin_chat_id": task.origin_chat_id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def _event_to_dict(ev) -> dict:
    return {
        "id": ev.id,
        "ts": ev.ts.isoformat(),
        "actor": ev.actor,
        "kind": ev.kind,
        "payload": ev.payload,
    }


def register(server: FastMCP, db_path: Path) -> None:
    repo = TaskRepository(db_path)

    @server.tool(name="tasks.create")
    async def tasks_create(
        title: str,
        description: str,
        origin_chat_id: int,
        parent_id: int | None = None,
        owner: str | None = None,
    ) -> dict:
        """Create a new task in 'pending' status. Returns the created task."""
        t = await repo.create(
            title=title, description=description, origin_chat_id=origin_chat_id,
            parent_id=parent_id, owner=owner,
        )
        return _task_to_dict(t)

    @server.tool(name="tasks.get")
    async def tasks_get(task_id: int) -> dict:
        """Returns the task and its 20 most recent events. {"error": ...} if unknown."""
        t = await repo.get(task_id)
        if t is None:
            return {"error": f"Unknown task {task_id}"}
        events = await repo.events(task_id, limit=20)
        return {"task": _task_to_dict(t), "recent_events": [_event_to_dict(e) for e in events]}

    @server.tool(name="tasks.list")
    async def tasks_list(
        status: str | None = None,
        owner: str | None = None,
        parent_id: int | None = None,
    ) -> list[dict]:
        """List tasks, optionally filtered by status/owner/parent_id."""
        status_enum = TaskStatus(status) if status else None
        tasks = await repo.list(status=status_enum, owner=owner, parent_id=parent_id)
        return [_task_to_dict(t) for t in tasks]

    @server.tool(name="tasks.tree")
    async def tasks_tree(task_id: int) -> dict:
        """Returns root + all descendants. {"error": ...} if root unknown."""
        result = await repo.tree(task_id)
        if result is None:
            return {"error": f"Unknown task {task_id}"}
        return {
            "root": _task_to_dict(result["root"]),
            "descendants": [_task_to_dict(t) for t in result["descendants"]],
        }

    @server.tool(name="tasks.update")
    async def tasks_update(
        task_id: int,
        status: str | None = None,
        owner: str | None = None,
        worktree_path: str | None = None,
        branch_name: str | None = None,
    ) -> dict:
        """Update task fields. Status changes are validated against the transition map."""
        try:
            status_enum = TaskStatus(status) if status else None
            t = await repo.update(
                task_id,
                status=status_enum,
                owner=owner,
                worktree_path=worktree_path,
                branch_name=branch_name,
            )
        except InvalidTransition as exc:
            return {"error": str(exc)}
        if t is None:
            return {"error": f"Unknown task {task_id}"}
        return _task_to_dict(t)

    @server.tool(name="tasks.comment")
    async def tasks_comment(task_id: int, body: str, actor: str = "agent") -> dict:
        """Append a comment event to the task. Returns the new event_id."""
        event_id = await repo.comment(task_id, actor=actor, body=body)
        return {"event_id": event_id}
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_mcp_tools_tasks.py -v`
Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/mcp_server/tools/tasks_tools.py tests/test_mcp_tools_tasks.py
git commit -m "feat(mcp): register tasks.* tools (create/get/list/tree/update/comment)"
```

---

## Task 18: MCP tool — handoff

**Files:**
- Modify: `agent_hub/mcp_server/tools/handoff_tool.py`
- Create: `tests/test_mcp_tools_handoff.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_mcp_tools_handoff.py`:

```python
import pytest
from mcp.server.fastmcp import FastMCP

from agent_hub.db import Database
from agent_hub.mcp_server.tools.handoff_tool import register
from agent_hub.mcp_server.tools.tasks_tools import register as register_tasks
from agent_hub.tasks.handoff_queue import HandoffQueue


@pytest.fixture
async def server_and_queue(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    server = FastMCP("test")
    register_tasks(server, temp_db_path)  # need tasks.create to set up
    register(server, temp_db_path)
    return server, HandoffQueue(temp_db_path)


def _tool(server: FastMCP, name: str):
    return server._tool_manager.get_tool(name).fn  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_handoff_enqueues_row(server_and_queue):
    server, queue = server_and_queue
    create = _tool(server, "tasks.create")
    handoff = _tool(server, "handoff")
    t = await create(title="x", description="-", origin_chat_id=1)
    result = await handoff(to_agent="architect", task_id=t["id"], message="design this", from_agent="pm")
    assert result["enqueued"] is True
    assert result["queue_id"] > 0

    pending = await queue.pending()
    assert len(pending) == 1
    assert pending[0].to_agent == "architect"
    assert pending[0].message == "design this"


@pytest.mark.asyncio
async def test_handoff_to_self_returns_error(server_and_queue):
    server, _ = server_and_queue
    create = _tool(server, "tasks.create")
    handoff = _tool(server, "handoff")
    t = await create(title="x", description="-", origin_chat_id=1)
    result = await handoff(to_agent="pm", task_id=t["id"], message="me", from_agent="pm")
    assert "error" in result
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_mcp_tools_handoff.py -v`
Expected: tool lookup fails.

- [ ] **Step 3: Implement handoff**

Replace contents of `agent_hub/mcp_server/tools/handoff_tool.py`:

```python
"""Handoff MCP tool — enqueues an agent-to-agent dispatch.

The orchestrator (later plan) pops these and routes them to the
target agent's session, prepending task context.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from agent_hub.tasks.handoff_queue import HandoffQueue


def register(server: FastMCP, db_path: Path) -> None:
    queue = HandoffQueue(db_path)

    @server.tool(name="handoff")
    async def handoff(to_agent: str, task_id: int, message: str, from_agent: str) -> dict:
        """Enqueue a handoff to another agent. Returns the queue_id.

        Self-handoff is disallowed — pass to a different agent or
        keep working in the current turn.
        """
        if to_agent == from_agent:
            return {"error": f"Cannot hand off to self ({from_agent}). Keep working or pass to a different agent."}
        qid = await queue.enqueue(
            task_id=task_id, from_agent=from_agent,
            to_agent=to_agent, message=message,
        )
        return {"enqueued": True, "queue_id": qid}
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_mcp_tools_handoff.py -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/mcp_server/tools/handoff_tool.py tests/test_mcp_tools_handoff.py
git commit -m "feat(mcp): register handoff tool with self-handoff guard"
```

---

## Task 19: MCP tools — gate.*

**Files:**
- Modify: `agent_hub/mcp_server/tools/gate_tools.py`
- Create: `tests/test_mcp_tools_gates.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_mcp_tools_gates.py`:

```python
import pytest
from mcp.server.fastmcp import FastMCP

from agent_hub.db import Database
from agent_hub.mcp_server.tools.gate_tools import register
from agent_hub.mcp_server.tools.tasks_tools import register as register_tasks
from agent_hub.tasks.gates import GateRepository


@pytest.fixture
async def server_and_gates(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    server = FastMCP("test")
    register_tasks(server, temp_db_path)
    register(server, temp_db_path)
    return server, GateRepository(temp_db_path)


def _tool(server: FastMCP, name: str):
    return server._tool_manager.get_tool(name).fn  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_gate_request_creates_pending(server_and_gates):
    server, gates = server_and_gates
    create = _tool(server, "tasks.create")
    gate_request = _tool(server, "gate.request")
    gate_status = _tool(server, "gate.status")
    t = await create(title="x", description="-", origin_chat_id=1)
    res = await gate_request(task_id=t["id"], kind="design", summary="please review")
    assert res["gate_id"] > 0
    s = await gate_status(task_id=t["id"], kind="design")
    assert s["status"] == "pending"


@pytest.mark.asyncio
async def test_gate_status_none_when_no_gate(server_and_gates):
    server, _ = server_and_gates
    create = _tool(server, "tasks.create")
    gate_status = _tool(server, "gate.status")
    t = await create(title="x", description="-", origin_chat_id=1)
    s = await gate_status(task_id=t["id"], kind="design")
    assert s["status"] == "none"
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_mcp_tools_gates.py -v`
Expected: tool lookup fails.

- [ ] **Step 3: Implement gate tools**

Replace contents of `agent_hub/mcp_server/tools/gate_tools.py`:

```python
"""Gate MCP tools — design approval lifecycle.

In v1 the only `kind` is "design". The architect calls gate.request
at the end of a design session; the orchestrator (later plan) sees
the pending row and DMs the user. The user's /approve or /reject
command resolves the gate from the orchestrator side — agents don't
resolve their own gates.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from agent_hub.tasks.gates import GateRepository


def register(server: FastMCP, db_path: Path) -> None:
    gates = GateRepository(db_path)

    @server.tool(name="gate.request")
    async def gate_request(
        task_id: int,
        kind: str = "design",
        artifact_path: str | None = None,
        summary: str | None = None,
    ) -> dict:
        """Request a human gate. Pauses the task until the user resolves."""
        if kind != "design":
            return {"error": f"Unknown gate kind {kind!r}. v1 supports only 'design'."}
        gid = await gates.request(
            task_id=task_id, kind=kind,
            artifact_path=artifact_path, summary=summary,
        )
        return {"gate_id": gid}

    @server.tool(name="gate.status")
    async def gate_status(task_id: int, kind: str = "design") -> dict:
        """Returns {'status': 'pending'|'approved'|'rejected'|'none'}."""
        s = await gates.status(task_id=task_id, kind=kind)
        return {"status": s}
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_mcp_tools_gates.py -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/mcp_server/tools/gate_tools.py tests/test_mcp_tools_gates.py
git commit -m "feat(mcp): register gate.request and gate.status tools"
```

---

## Task 20: MCP server end-to-end stdio test

**Files:**
- Create: `tests/test_mcp_server_e2e.py`

This test actually launches `python -m agent_hub.mcp_server` as a subprocess and calls a tool through the MCP client transport — proves the wiring works end-to-end.

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_server_e2e.py`:

```python
"""End-to-end stdio test: spawn the MCP server as a subprocess and
exercise a real tool call through the official client.
"""

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_hub.db import Database


@pytest.mark.asyncio
async def test_e2e_create_task_via_mcp(temp_db_path):
    db = Database(temp_db_path)
    await db.init()

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_hub.mcp_server"],
        env={**os.environ, "AGENT_HUB_DB": str(temp_db_path)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "tasks.create" in names
            assert "handoff" in names
            assert "gate.request" in names

            result = await session.call_tool(
                "tasks.create",
                {"title": "e2e", "description": "-", "origin_chat_id": 1},
            )
            # FastMCP wraps the return; the content[0].text is the JSON-encoded dict.
            import json
            payload = json.loads(result.content[0].text)
            assert payload["id"] > 0
            assert payload["status"] == "pending"
```

- [ ] **Step 2: Run, verify**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_mcp_server_e2e.py -v`
Expected: PASS.

If the test fails because FastMCP wraps the response differently than the assertion expects, inspect `result.content[0]` and adjust the parsing — the parser is the only fragile bit. Document the actual shape in a code comment.

- [ ] **Step 3: Commit**

```bash
git add tests/test_mcp_server_e2e.py
git commit -m "test(mcp): end-to-end subprocess + stdio round-trip"
```

---

## Task 21: Run full suite + record baseline

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `.\.venv\Scripts\python.exe -m pytest -v`
Expected: ALL pass. Tally roughly:
- `test_smoke.py` — 1
- `test_schema.py` — 5
- `test_state_machine.py` — 12
- `test_tasks_models.py` — 5
- `test_tasks_repository.py` — 16
- `test_handoff_queue.py` — 6
- `test_gates.py` — 5
- `test_mcp_server_import.py` — 1
- `test_mcp_tools_tasks.py` — 7
- `test_mcp_tools_handoff.py` — 2
- `test_mcp_tools_gates.py` — 2
- `test_mcp_server_e2e.py` — 1
- **Total: ~63 tests passing**

If any fail, debug to green before continuing.

- [ ] **Step 2: Run in parallel to verify isolation**

Run: `.\.venv\Scripts\python.exe -m pytest -n auto`
Expected: same count, all pass — proves no test depends on shared state.

- [ ] **Step 3: Commit (any incidental fixes only)**

If steps 1–2 surfaced no failures, no commit needed.

---

## Self-review

**Spec coverage:**
- §4.1 schema additions — Tasks 3, 4, 5 ✓
- §4.2 state machine — Tasks 6, 7 ✓
- §4.3 MCP tools (tasks.*, handoff, gate.*) — Tasks 17, 18, 19 ✓
- §4.3 MCP server skeleton — Task 16 ✓
- §4.3 `worktree.*` tools — **deferred to Plan 2** (depends on worktree manager)
- Tier 1 tests for: tasks repo, handoff queue, gates, MCP tools, transition map, schema — all present ✓
- §6.B "atomic claim" — Task 14 race test ✓
- §6.B "transition validator" — Task 11 invalid-transition test ✓

**Placeholder scan:** none. Every step has runnable code or a runnable command. The "implement later" `register()` stubs in Task 16 are placeholders only in the temporary sense — Tasks 17-19 fill them in the same plan.

**Type consistency:**
- `TaskStatus.PENDING.value == "pending"` (StrEnum) — used as DB string in repo, parsed back via `TaskStatus(row[4])` ✓
- `TaskRepository.update(...)` signature consistent across Tasks 11, 12, 17 ✓
- `HandoffRow.claimed_at: datetime | None` consistent across enqueue/claim/pending ✓
- MCP tool names canonical: `tasks.create`, `tasks.get`, `tasks.list`, `tasks.tree`, `tasks.update`, `tasks.comment`, `handoff`, `gate.request`, `gate.status` ✓

**Known sequencing notes for the executor:**
- Task 16's `register()` stubs MUST exist before Tasks 17-19 — that's why Task 16 creates all three stub files in one step.
- Task 12 modifies `update()` to write a `status_change` event. The append helper is also used by future plans (push events, gate_resolve events). Keep `_append_event` private but stable.
- The `FastMCP._tool_manager` access in tests reaches into a private API. If the mcp SDK changes, only the `_tool(server, name)` helper needs to be updated.
