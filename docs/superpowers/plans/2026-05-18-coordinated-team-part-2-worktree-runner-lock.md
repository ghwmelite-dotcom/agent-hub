# Coordinated team — Part 2: Worktree manager + Runner pool + Lock file

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the workspace + execution layer that sits on top of Plan 1's data foundation: a `WorktreeManager` for per-task git worktrees, a `WorktreeRepository` for the `worktrees` table, a per-(agent, task) client pool in `AgentRunner` with MCP server injection, and an `.orchestrator.lock` pidfile so two `agent_hub` processes can't fight over the same database.

**Architecture:** Three independent components plus one integration step. The lock file uses `psutil` for cross-platform PID liveness checks. The worktree manager wraps `git worktree add/remove` subprocesses and records state in SQLite via `WorktreeRepository`. The runner stops caching one SDK client per agent and starts caching one per (agent, task), with each client's `cwd` set to the task's worktree and `ClaudeAgentOptions.mcp_servers` configured to launch `agent-hub-mcp` with the right DB path. `agent_hub/__main__.py` acquires the lock at startup and exports `AGENT_HUB_DB` for the SDK to inherit.

**Tech Stack:** Python 3.14, `psutil` (new), `aiosqlite`, `claude-agent-sdk`, `pytest` + `pytest-asyncio`.

**Source spec:** `docs/superpowers/specs/2026-05-17-coordinated-agent-team-design.md` (sections 4.4 — AgentRunner updates; 4.6 — Worktree manager; 6.D — single orchestrator lock).

**Source plan dependencies:** Plan 1 (`docs/superpowers/plans/2026-05-17-coordinated-team-part-1-data-foundation.md`) — provides the `tasks`, `worktrees` tables; the `TaskStatus` enum; the MCP server entry point at `python -m agent_hub.mcp_server`.

**Not in this plan (deferred):**
- Orchestrator handoff loop / gate watcher / push action / epic completion (Plan 3).
- Telegram command handlers (Plan 3).
- Agent prompt updates (Plan 4).
- Integration tests using `FakeAgentRunner` (Plan 3 — `FakeAgentRunner` lands there).
- Real SDK end-to-end smoke (Plan 4).
- Spend cap, stuck-loop detection, restart-resume — all Plan 4.

---

## File structure produced by this plan

```
agent_hub/
  orchestrator/
    lock.py                      # CREATE: pidfile-based single-instance lock
  worktree_manager.py            # CREATE: WorktreeManager wrapping git worktree subprocesses
  tasks/
    worktree_repo.py             # CREATE: WorktreeRepository (CRUD for worktrees table)
  agents/
    runner.py                    # MODIFY: per-(agent, task) pool + MCP injection + task_id-aware send()
    runner_options.py            # CREATE: pure option-builder helper (testable without SDK)
  __main__.py                    # MODIFY: acquire lock; export AGENT_HUB_DB env var; pass to runner

tests/
  test_lock.py                   # CREATE: acquire / refuse-live / steal-stale / context manager
  test_worktree_repo.py          # CREATE: record / get / mark_cleaned / list_active / list_orphans
  test_worktree_manager.py       # CREATE: branch slug / create / path / cleanup
  test_runner_options.py         # CREATE: build_options shape + MCP config
  test_runner_pool.py            # CREATE: per-(agent, task) cache-key behaviour (mocks SDK)
```

---

## Conventions used in every task

- **TDD pattern:** write failing test → verify it fails → minimal implementation → verify it passes → commit.
- **Test runner:** `.\.venv\Scripts\python.exe -m pytest` (always use the venv python, never system Python).
- **Commit style:** Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`, `refactor:`). One concept per commit.
- **DB connection pattern (carried from Plan 1):** `_connect()` is sync, returns `aiosqlite.connect(...)`. Inside each `async with self._connect() as conn:` block, first `await conn.execute("PRAGMA foreign_keys = ON")` then `conn.row_factory = aiosqlite.Row`. Use named row access.
- **MCP tool errors (carried from Plan 1):** any new MCP tools added in this plan should use `@safe_tool` from `agent_hub/mcp_server/tools/_safe.py`. (Plan 2 does not add new MCP tools, but if you do, follow the pattern.)
- **Subprocess git calls:** use `asyncio.create_subprocess_exec` so the runner stays cooperative.

---

## Task 0: Add psutil to requirements

**Files:** Modify `requirements.txt`; modify `pyproject.toml`.

- [ ] **Step 1: Append psutil to requirements.txt**

Add this line to `requirements.txt` (after `mcp>=1.27.0`):

```
psutil>=6.0.0
```

- [ ] **Step 2: Add psutil to pyproject.toml**

In `pyproject.toml`, add `"psutil>=6.0.0"` to `[project.dependencies]`.

- [ ] **Step 3: Install**

Run: `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`
Expected: `Successfully installed psutil-...` (or "Requirement already satisfied").

- [ ] **Step 4: Verify importable**

Run: `.\.venv\Scripts\python.exe -c "import psutil; print(psutil.pid_exists(1))"`
Expected: prints `True` (PID 1 always exists on a running OS).

- [ ] **Step 5: Commit**

```bash
git add requirements.txt pyproject.toml
git commit -m "chore: add psutil for cross-platform PID liveness checks"
```

---

## Task 1: Orchestrator lock file — acquire on fresh path

**Files:**
- Create: `agent_hub/orchestrator/lock.py`
- Create: `tests/test_lock.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_lock.py`:

```python
import os
from pathlib import Path

import pytest

from agent_hub.orchestrator.lock import OrchestratorLock, LockHeld


def test_acquire_writes_pid_to_file(tmp_path: Path):
    lock_path = tmp_path / ".orchestrator.lock"
    lock = OrchestratorLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.exists()
        content = lock_path.read_text().strip()
        assert content == str(os.getpid())
    finally:
        lock.release()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_lock.py -v`
Expected: `ModuleNotFoundError: No module named 'agent_hub.orchestrator.lock'`.

- [ ] **Step 3: Implement minimal lock**

Create `agent_hub/orchestrator/lock.py`:

```python
"""Single-instance lock for agent_hub via a pidfile.

Prevents two agent_hub processes from racing on the same data/ dir.
On acquire, writes the current PID; if the file already exists and
the recorded PID is still alive, raises LockHeld. Stale lockfiles
(PID dead) are stolen.

Cross-platform PID liveness via psutil.pid_exists().
"""

from __future__ import annotations

import os
from pathlib import Path

import psutil


class LockHeld(RuntimeError):
    """Raised when another agent_hub process holds the lock."""


class OrchestratorLock:
    def __init__(self, path: Path):
        self.path = path
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()))
        self._owned = True

    def release(self) -> None:
        if self._owned and self.path.exists():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._owned = False
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_lock.py -v`
Expected: 1 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/lock.py tests/test_lock.py
git commit -m "feat(lock): add OrchestratorLock skeleton (acquire writes PID)"
```

---

## Task 2: Lock refuses when live PID holds it

**Files:**
- Modify: `agent_hub/orchestrator/lock.py`
- Modify: `tests/test_lock.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_lock.py`:

```python
def test_acquire_refuses_when_live_pid_holds_it(tmp_path: Path):
    """If the lock exists and contains a live PID, acquire must raise."""
    lock_path = tmp_path / ".orchestrator.lock"
    # Simulate another agent_hub holding the lock — write our own pid
    # since it's guaranteed live.
    lock_path.write_text(str(os.getpid()))

    lock = OrchestratorLock(lock_path)
    with pytest.raises(LockHeld) as exc:
        lock.acquire()
    assert str(os.getpid()) in str(exc.value)
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_lock.py::test_acquire_refuses_when_live_pid_holds_it -v`
Expected: the test fails because `acquire()` silently overwrites the file.

- [ ] **Step 3: Add liveness check to acquire()**

Replace the body of `acquire()` in `agent_hub/orchestrator/lock.py`:

```python
    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            existing = self.path.read_text().strip()
            try:
                existing_pid = int(existing)
            except ValueError:
                existing_pid = None
            if existing_pid is not None and psutil.pid_exists(existing_pid):
                raise LockHeld(
                    f"Lock {self.path} held by live PID {existing_pid}. "
                    f"Stop the other agent_hub process or remove the lock file."
                )
            # Stale — fall through to overwrite.
        self.path.write_text(str(os.getpid()))
        self._owned = True
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_lock.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/lock.py tests/test_lock.py
git commit -m "feat(lock): refuse acquire when a live PID holds the lock"
```

---

## Task 3: Lock steals stale lockfiles

**Files:** Modify `tests/test_lock.py`.

- [ ] **Step 1: Add failing test**

Append to `tests/test_lock.py`:

```python
def test_acquire_steals_when_stale_pid(tmp_path: Path, monkeypatch):
    """If the recorded PID is dead, the lock is stolen."""
    lock_path = tmp_path / ".orchestrator.lock"
    lock_path.write_text("999999999")  # vanishingly unlikely to be alive
    monkeypatch.setattr("psutil.pid_exists", lambda pid: False)  # force dead

    lock = OrchestratorLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_acquire_steals_when_garbage_contents(tmp_path: Path):
    """If the lock file is unreadable as a PID, steal it (treat as stale)."""
    lock_path = tmp_path / ".orchestrator.lock"
    lock_path.write_text("not-a-number")

    lock = OrchestratorLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()
```

- [ ] **Step 2: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_lock.py -v`
Expected: 4 PASS — the steal-stale behaviour already works because Task 2's `acquire()` falls through when `pid_exists` is False or `existing_pid is None`.

If a test surprisingly fails: debug — the test design should reveal the bug, do not loosen the assertion.

- [ ] **Step 3: Commit (test-only)**

```bash
git add tests/test_lock.py
git commit -m "test(lock): cover stale-PID and garbage-contents steal paths"
```

---

## Task 4: Lock as a context manager

**Files:**
- Modify: `agent_hub/orchestrator/lock.py`
- Modify: `tests/test_lock.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_lock.py`:

```python
def test_context_manager_releases_on_exit(tmp_path: Path):
    lock_path = tmp_path / ".orchestrator.lock"
    with OrchestratorLock(lock_path):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_context_manager_releases_on_exception(tmp_path: Path):
    lock_path = tmp_path / ".orchestrator.lock"
    with pytest.raises(RuntimeError):
        with OrchestratorLock(lock_path):
            assert lock_path.exists()
            raise RuntimeError("boom")
    assert not lock_path.exists()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_lock.py -v`
Expected: 2 new tests FAIL (no `__enter__` / `__exit__`).

- [ ] **Step 3: Add context manager methods**

Append to the `OrchestratorLock` class in `agent_hub/orchestrator/lock.py`:

```python
    def __enter__(self) -> "OrchestratorLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_lock.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/lock.py tests/test_lock.py
git commit -m "feat(lock): support context manager interface"
```

---

## Task 5: WorktreeRepository — record + get_by_task

**Files:**
- Create: `agent_hub/tasks/worktree_repo.py`
- Create: `tests/test_worktree_repo.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_worktree_repo.py`:

```python
import pytest

from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), WorktreeRepository(temp_db_path)


@pytest.mark.asyncio
async def test_record_inserts_row(deps):
    repo, worktrees = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await worktrees.record(
        task_id=t.id,
        path="/tmp/wt/42",
        branch="task/42-x",
        base_branch="main",
    )
    row = await worktrees.get_by_task(t.id)
    assert row is not None
    assert row.path == "/tmp/wt/42"
    assert row.branch == "task/42-x"
    assert row.base_branch == "main"
    assert row.cleaned_at is None


@pytest.mark.asyncio
async def test_get_by_task_returns_none_when_no_row(deps):
    repo, worktrees = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    assert await worktrees.get_by_task(t.id) is None
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_repo.py -v`
Expected: import error.

- [ ] **Step 3: Add a Worktree pydantic model**

Append to `agent_hub/tasks/models.py`:

```python
class Worktree(BaseModel):
    """Row from the worktrees table.

    One row per task, keyed on task_id (PK). cleaned_at is set when
    the worktree is removed from disk after the task is done.
    """
    task_id: int
    path: str
    branch: str
    base_branch: str
    created_at: datetime
    cleaned_at: datetime | None = None
```

- [ ] **Step 4: Implement repository**

Create `agent_hub/tasks/worktree_repo.py`:

```python
"""SQLite repository for the worktrees table.

One row per task. The WorktreeManager (agent_hub/worktree_manager.py)
uses this for state; the orchestrator (Plan 3) reads it during
restart-resume and orphan detection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from agent_hub.tasks.models import Worktree


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


_COLS = "task_id, path, branch, base_branch, created_at, cleaned_at"


def _row_to_worktree(row) -> Worktree:
    return Worktree(
        task_id=row["task_id"],
        path=row["path"],
        branch=row["branch"],
        base_branch=row["base_branch"],
        created_at=_parse_dt(row["created_at"]),
        cleaned_at=_parse_dt(row["cleaned_at"]) if row["cleaned_at"] else None,
    )


class WorktreeRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _connect(self) -> Any:
        return aiosqlite.connect(self.db_path)

    async def record(
        self, *, task_id: int, path: str, branch: str, base_branch: str,
    ) -> None:
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                "INSERT INTO worktrees (task_id, path, branch, base_branch, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, path, branch, base_branch, _utcnow_iso()),
            )
            await conn.commit()

    async def get_by_task(self, task_id: int) -> Worktree | None:
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                f"SELECT {_COLS} FROM worktrees WHERE task_id = ?", (task_id,),
            )
            row = await cur.fetchone()
        return _row_to_worktree(row) if row else None
```

- [ ] **Step 5: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_repo.py -v`
Expected: 2 PASS.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/tasks/worktree_repo.py agent_hub/tasks/models.py tests/test_worktree_repo.py
git commit -m "feat(worktree-repo): add Worktree model and WorktreeRepository.record/get_by_task"
```

---

## Task 6: WorktreeRepository — mark_cleaned + list_active

**Files:**
- Modify: `agent_hub/tasks/worktree_repo.py`
- Modify: `tests/test_worktree_repo.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_worktree_repo.py`:

```python
@pytest.mark.asyncio
async def test_mark_cleaned_sets_timestamp(deps):
    repo, worktrees = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await worktrees.record(task_id=t.id, path="/tmp/wt/42", branch="task/42-x", base_branch="main")
    await worktrees.mark_cleaned(t.id)
    row = await worktrees.get_by_task(t.id)
    assert row is not None
    assert row.cleaned_at is not None


@pytest.mark.asyncio
async def test_list_active_excludes_cleaned(deps):
    repo, worktrees = deps
    t1 = await repo.create(title="a", description="-", origin_chat_id=1)
    t2 = await repo.create(title="b", description="-", origin_chat_id=1)
    await worktrees.record(task_id=t1.id, path="/tmp/wt/1", branch="task/1-a", base_branch="main")
    await worktrees.record(task_id=t2.id, path="/tmp/wt/2", branch="task/2-b", base_branch="main")
    await worktrees.mark_cleaned(t2.id)

    active = await worktrees.list_active()
    active_ids = {w.task_id for w in active}
    assert active_ids == {t1.id}


@pytest.mark.asyncio
async def test_mark_cleaned_idempotent(deps):
    repo, worktrees = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await worktrees.record(task_id=t.id, path="/tmp/wt/42", branch="task/42-x", base_branch="main")
    await worktrees.mark_cleaned(t.id)
    first_cleaned_at = (await worktrees.get_by_task(t.id)).cleaned_at
    # Second call should not change the timestamp.
    await worktrees.mark_cleaned(t.id)
    second_cleaned_at = (await worktrees.get_by_task(t.id)).cleaned_at
    assert first_cleaned_at == second_cleaned_at
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_repo.py -v`
Expected: 3 new tests FAIL (methods don't exist).

- [ ] **Step 3: Implement mark_cleaned + list_active**

Append to `WorktreeRepository` in `agent_hub/tasks/worktree_repo.py`:

```python
    async def mark_cleaned(self, task_id: int) -> None:
        """Set cleaned_at to now. Idempotent — second call is a no-op."""
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            await conn.execute(
                "UPDATE worktrees SET cleaned_at = ? "
                "WHERE task_id = ? AND cleaned_at IS NULL",
                (_utcnow_iso(), task_id),
            )
            await conn.commit()

    async def list_active(self) -> list[Worktree]:
        """All worktrees whose cleaned_at IS NULL (i.e. still on disk)."""
        async with self._connect() as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                f"SELECT {_COLS} FROM worktrees WHERE cleaned_at IS NULL "
                "ORDER BY task_id ASC"
            )
            rows = await cur.fetchall()
        return [_row_to_worktree(r) for r in rows]
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_repo.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/tasks/worktree_repo.py tests/test_worktree_repo.py
git commit -m "feat(worktree-repo): add mark_cleaned (idempotent) and list_active"
```

---

## Task 7: WorktreeManager — branch slug helper

**Files:**
- Create: `agent_hub/worktree_manager.py`
- Create: `tests/test_worktree_manager.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_worktree_manager.py`:

```python
from agent_hub.worktree_manager import branch_slug


def test_branch_slug_simple_title():
    assert branch_slug(42, "add health endpoint") == "task/42-add-health-endpoint"


def test_branch_slug_lowercases():
    assert branch_slug(7, "Add Login") == "task/7-add-login"


def test_branch_slug_strips_special_chars():
    assert branch_slug(1, "Fix bug: @user/path!") == "task/1-fix-bug-user-path"


def test_branch_slug_collapses_whitespace():
    assert branch_slug(1, "  many   spaces  ") == "task/1-many-spaces"


def test_branch_slug_truncates_long_titles():
    long_title = "a" * 200
    slug = branch_slug(99, long_title)
    # Total slug must fit reasonably; we cap the title portion at 60 chars.
    parts = slug.split("/", 1)
    assert parts[0] == "task"
    rest = parts[1]
    # rest is "99-<title-portion>"; cap on title portion is 60 chars.
    title_portion = rest.split("-", 1)[1]
    assert len(title_portion) <= 60


def test_branch_slug_unicode_falls_back_to_id():
    # If the title slugifies to empty, fall back to just the id.
    assert branch_slug(5, "🎉🎊") == "task/5"
    assert branch_slug(6, "") == "task/6"
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_manager.py -v`
Expected: import error.

- [ ] **Step 3: Implement branch_slug**

Create `agent_hub/worktree_manager.py`:

```python
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
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_manager.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/worktree_manager.py tests/test_worktree_manager.py
git commit -m "feat(worktrees): add branch_slug helper with safe normalisation"
```

---

## Task 8: WorktreeManager — create

**Files:**
- Modify: `agent_hub/worktree_manager.py`
- Modify: `tests/test_worktree_manager.py`

- [ ] **Step 1: Add fixture + failing test**

Append to `tests/test_worktree_manager.py`:

```python
import asyncio
import subprocess
from pathlib import Path

import pytest

from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository
from agent_hub.worktree_manager import WorktreeManager


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialise a fresh git repo with an initial commit on main."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "t@example.com"], cwd=repo)
    (repo / "README.md").write_text("hello\n")
    subprocess.check_call(["git", "add", "README.md"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "initial"], cwd=repo)
    return repo


@pytest.fixture
async def manager_deps(temp_db_path, git_repo, tmp_path):
    db = Database(temp_db_path)
    await db.init()
    worktrees_root = tmp_path / "worktrees"
    manager = WorktreeManager(
        repo_root=git_repo,
        worktrees_root=worktrees_root,
        db_path=temp_db_path,
    )
    repo = TaskRepository(temp_db_path)
    return manager, repo, worktrees_root


@pytest.mark.asyncio
async def test_create_makes_worktree_and_branch(manager_deps):
    manager, repo, worktrees_root = manager_deps
    task = await repo.create(title="add health", description="-", origin_chat_id=1)

    result = await manager.create(task_id=task.id, title=task.title, base_branch="main")

    assert result["branch"] == f"task/{task.id}-add-health"
    expected_path = worktrees_root / str(task.id)
    assert Path(result["path"]) == expected_path
    assert expected_path.exists()
    # README from main should be visible inside the worktree
    assert (expected_path / "README.md").exists()


@pytest.mark.asyncio
async def test_create_records_db_row(manager_deps):
    manager, repo, _ = manager_deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await manager.create(task_id=task.id, title=task.title, base_branch="main")

    from agent_hub.tasks.worktree_repo import WorktreeRepository
    wt_repo = WorktreeRepository(manager.db_path)
    row = await wt_repo.get_by_task(task.id)
    assert row is not None
    assert row.branch == f"task/{task.id}-x"
    assert row.cleaned_at is None
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_manager.py -v`
Expected: 2 new tests FAIL (no `WorktreeManager` class).

- [ ] **Step 3: Implement WorktreeManager.create**

Append to `agent_hub/worktree_manager.py`:

```python
import asyncio
from pathlib import Path

from agent_hub.tasks.worktree_repo import WorktreeRepository


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
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_manager.py -v`
Expected: 8 PASS (6 from Task 7 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add agent_hub/worktree_manager.py tests/test_worktree_manager.py
git commit -m "feat(worktrees): add WorktreeManager.create wrapping `git worktree add`"
```

---

## Task 9: WorktreeManager — path lookup

**Files:**
- Modify: `agent_hub/worktree_manager.py`
- Modify: `tests/test_worktree_manager.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_worktree_manager.py`:

```python
@pytest.mark.asyncio
async def test_path_returns_recorded_path(manager_deps):
    manager, repo, worktrees_root = manager_deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    created = await manager.create(task_id=task.id, title=task.title)

    looked_up = await manager.path(task.id)
    assert looked_up == created["path"]


@pytest.mark.asyncio
async def test_path_returns_none_for_unknown_task(manager_deps):
    manager, _, _ = manager_deps
    assert await manager.path(99999) is None
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_manager.py -v`
Expected: 2 new FAIL (no `path` method).

- [ ] **Step 3: Implement path**

Append to `WorktreeManager` in `agent_hub/worktree_manager.py`:

```python
    async def path(self, task_id: int) -> str | None:
        """Return the recorded worktree path for task_id, or None."""
        row = await self._repo.get_by_task(task_id)
        return row.path if row else None
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_manager.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/worktree_manager.py tests/test_worktree_manager.py
git commit -m "feat(worktrees): add WorktreeManager.path lookup"
```

---

## Task 10: WorktreeManager — cleanup with dirty guard

**Files:**
- Modify: `agent_hub/worktree_manager.py`
- Modify: `tests/test_worktree_manager.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_worktree_manager.py`:

```python
@pytest.mark.asyncio
async def test_cleanup_removes_worktree_and_marks_db(manager_deps):
    manager, repo, _ = manager_deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    created = await manager.create(task_id=task.id, title=task.title)
    assert Path(created["path"]).exists()

    await manager.cleanup(task.id)

    assert not Path(created["path"]).exists()
    from agent_hub.tasks.worktree_repo import WorktreeRepository
    wt_repo = WorktreeRepository(manager.db_path)
    row = await wt_repo.get_by_task(task.id)
    assert row.cleaned_at is not None


@pytest.mark.asyncio
async def test_cleanup_refuses_dirty_worktree(manager_deps):
    manager, repo, _ = manager_deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    created = await manager.create(task_id=task.id, title=task.title)
    # Make the worktree dirty.
    (Path(created["path"]) / "dirt.txt").write_text("uncommitted\n")

    with pytest.raises(RuntimeError) as exc:
        await manager.cleanup(task.id)
    assert "uncommitted" in str(exc.value).lower() or "dirty" in str(exc.value).lower()
    # Worktree must still exist after refused cleanup.
    assert Path(created["path"]).exists()


@pytest.mark.asyncio
async def test_cleanup_unknown_task_is_noop(manager_deps):
    manager, _, _ = manager_deps
    # Should not raise; no worktree to clean.
    await manager.cleanup(99999)
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_manager.py -v`
Expected: 3 new FAIL.

- [ ] **Step 3: Implement cleanup**

Append to `WorktreeManager` in `agent_hub/worktree_manager.py`:

```python
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
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_manager.py -v`
Expected: 13 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/worktree_manager.py tests/test_worktree_manager.py
git commit -m "feat(worktrees): add cleanup with dirty-worktree guard"
```

---

## Task 11: Runner option builder (pure, testable)

**Files:**
- Create: `agent_hub/agents/runner_options.py`
- Create: `tests/test_runner_options.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_runner_options.py`:

```python
from pathlib import Path

from agent_hub.agents.registry import AgentRole
from agent_hub.agents.runner_options import build_mcp_server_config, build_sdk_options


def _role() -> AgentRole:
    return AgentRole(
        name="pm",
        display_name="PM",
        aliases=["pm"],
        model="claude-sonnet-4-6",
        allowed_tools=["Read", "Bash"],
        system_prompt="You are PM.",
    )


def test_build_mcp_server_config_shape(tmp_path: Path):
    db_path = tmp_path / "agent_hub.db"
    config = build_mcp_server_config(db_path)
    # Expected shape: dict with "agent_hub" key mapping to a stdio launch spec.
    assert "agent_hub" in config
    spec = config["agent_hub"]
    assert spec["command"]  # python executable
    assert spec["args"] == ["-m", "agent_hub.mcp_server"]
    assert spec["env"]["AGENT_HUB_DB"] == str(db_path)


def test_build_sdk_options_uses_role_fields(tmp_path: Path):
    role = _role()
    opts = build_sdk_options(role, cwd=None, db_path=tmp_path / "x.db")
    # We don't bind to the exact ClaudeAgentOptions API surface — just
    # check the attributes we care about. Whatever object is returned
    # must carry these fields.
    assert opts.system_prompt == "You are PM."
    assert opts.model == "claude-sonnet-4-6"
    assert set(opts.allowed_tools) == {"Read", "Bash"}
    assert opts.cwd is None


def test_build_sdk_options_sets_cwd_when_given(tmp_path: Path):
    role = _role()
    cwd = tmp_path / "wt" / "1"
    cwd.mkdir(parents=True)
    opts = build_sdk_options(role, cwd=cwd, db_path=tmp_path / "x.db")
    assert opts.cwd == str(cwd)


def test_build_sdk_options_includes_mcp_servers(tmp_path: Path):
    role = _role()
    db_path = tmp_path / "x.db"
    opts = build_sdk_options(role, cwd=None, db_path=db_path)
    # mcp_servers must include the agent_hub entry.
    assert "agent_hub" in opts.mcp_servers
    assert opts.mcp_servers["agent_hub"]["env"]["AGENT_HUB_DB"] == str(db_path)
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_runner_options.py -v`
Expected: import error.

- [ ] **Step 3: Implement runner_options**

Create `agent_hub/agents/runner_options.py`:

```python
"""Pure helpers that build the ClaudeSDKClient option payload.

Separated from runner.py so they can be tested without instantiating
the SDK client (which would spawn a subprocess).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agent_hub.agents.registry import AgentRole


def build_mcp_server_config(db_path: Path) -> dict[str, Any]:
    """The stdio launch spec passed to ClaudeAgentOptions.mcp_servers.

    Keyed under "agent_hub" so MCP tool names land in the
    "mcp__agent_hub__*" namespace.
    """
    return {
        "agent_hub": {
            "command": sys.executable,
            "args": ["-m", "agent_hub.mcp_server"],
            "env": {"AGENT_HUB_DB": str(db_path)},
        },
    }


def build_sdk_options(role: AgentRole, *, cwd: Path | None, db_path: Path) -> Any:
    """Construct a ClaudeAgentOptions for the given role + workspace.

    Returns the SDK's options object (whose exact class lives in
    claude_agent_sdk). Keeping the SDK import lazy here so test-time
    import of this module is cheap.
    """
    import claude_agent_sdk as sdk
    return sdk.ClaudeAgentOptions(
        system_prompt=role.system_prompt,
        allowed_tools=role.allowed_tools,
        model=role.model,
        cwd=str(cwd) if cwd else None,
        mcp_servers=build_mcp_server_config(db_path),
    )
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_runner_options.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/agents/runner_options.py tests/test_runner_options.py
git commit -m "feat(runner): extract build_sdk_options + build_mcp_server_config helpers"
```

---

## Task 12: Runner per-(agent, task) client pool

**Files:**
- Modify: `agent_hub/agents/runner.py`
- Create: `tests/test_runner_pool.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_runner_pool.py`:

```python
"""Tests for the per-(agent, task) client pool keying.

We do NOT spin up real ClaudeSDKClients here — we monkey-patch the
runner's internal SDK factory so we can observe how the pool is keyed
and how cwd flows through. Real-SDK tests land in Plan 3 (FakeAgentRunner)
and Plan 4 (Haiku smoke).
"""

from pathlib import Path

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.agents.runner import AgentRunner
from agent_hub.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="dummy",
        telegram_allowed_user_id=1,
        agent_workspaces=[],
        database_path=tmp_path / "agent_hub.db",
    )


class _FakeClient:
    def __init__(self, options):
        self.options = options
        self.connected = False
        self.disconnected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
def patched_runner(monkeypatch, tmp_path):
    """A runner whose SDK is mocked. Returns (runner, created_clients_list)."""
    created: list[_FakeClient] = []

    def fake_client_factory(options):
        c = _FakeClient(options)
        created.append(c)
        return c

    # Patch the symbol the runner imports.
    monkeypatch.setattr(
        "agent_hub.agents.runner._client_factory", fake_client_factory, raising=False
    )

    registry = AgentRegistry.load()
    runner = AgentRunner(settings=_settings(tmp_path), registry=registry)
    return runner, created


@pytest.mark.asyncio
async def test_get_client_caches_per_agent_when_no_task_id(patched_runner):
    runner, created = patched_runner
    c1 = await runner._get_or_create_client("pm", task_id=None, cwd=None)
    c2 = await runner._get_or_create_client("pm", task_id=None, cwd=None)
    # Same call, same key → reused.
    assert c1 is c2
    assert len(created) == 1


@pytest.mark.asyncio
async def test_get_client_keys_by_task_id(patched_runner):
    runner, created = patched_runner
    c_task5 = await runner._get_or_create_client("pm", task_id=5, cwd=None)
    c_task7 = await runner._get_or_create_client("pm", task_id=7, cwd=None)
    # Different task_id → different client.
    assert c_task5 is not c_task7
    assert len(created) == 2


@pytest.mark.asyncio
async def test_get_client_different_agents_different_clients(patched_runner):
    runner, created = patched_runner
    pm_client = await runner._get_or_create_client("pm", task_id=5, cwd=None)
    arch_client = await runner._get_or_create_client("architect", task_id=5, cwd=None)
    assert pm_client is not arch_client
    assert len(created) == 2


@pytest.mark.asyncio
async def test_get_client_passes_cwd_into_options(patched_runner, tmp_path):
    runner, created = patched_runner
    cwd = tmp_path / "wt" / "5"
    cwd.mkdir(parents=True)
    client = await runner._get_or_create_client("pm", task_id=5, cwd=cwd)
    assert client.options.cwd == str(cwd)


@pytest.mark.asyncio
async def test_shutdown_disconnects_all_pool_entries(patched_runner):
    runner, created = patched_runner
    await runner._get_or_create_client("pm", task_id=1, cwd=None)
    await runner._get_or_create_client("pm", task_id=2, cwd=None)
    await runner._get_or_create_client("architect", task_id=1, cwd=None)
    await runner.shutdown()
    assert all(c.disconnected for c in created)
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_runner_pool.py -v`
Expected: tests fail — `_get_or_create_client` doesn't exist; `_client_factory` symbol doesn't exist.

- [ ] **Step 3: Refactor runner to per-(agent, task) pool**

Open `agent_hub/agents/runner.py`. Make these changes:

(a) Replace the lazy SDK import at the top with a module-level factory hook:

```python
# Replace the existing `_sdk` / `_load_sdk` indirection. The runner now
# calls a module-level _client_factory(options) -> ClaudeSDKClient
# function, which tests can patch.

def _client_factory(options):
    """Default factory — constructs the real ClaudeSDKClient.

    Tests monkey-patch this symbol to inject a fake client.
    """
    import claude_agent_sdk as sdk
    return sdk.ClaudeSDKClient(options=options)
```

(b) Change the `_clients` dict to be keyed on `(agent_name, task_id)`:

```python
self._clients: dict[tuple[str, int | None], Any] = {}
```

(c) Add `_get_or_create_client` and update `send`:

```python
    async def _get_or_create_client(
        self,
        agent_name: str,
        *,
        task_id: int | None,
        cwd: Path | None,
    ) -> Any:
        from agent_hub.agents.runner_options import build_sdk_options

        role = self.registry.get(agent_name)
        key = (role.name, task_id)
        async with self._lock:
            if key in self._clients:
                return self._clients[key]
            options = build_sdk_options(role, cwd=cwd, db_path=self.settings.database_path)
            client = _client_factory(options)
            await client.connect()
            self._clients[key] = client
            log.info(
                "agent.started",
                agent=role.name,
                task_id=task_id,
                model=role.model,
                tools=role.allowed_tools,
                cwd=str(cwd) if cwd else None,
            )
            return client
```

(d) Update `send(agent_name, message, task_id=None)` to call `_get_or_create_client` with the task_id. The cwd lookup itself lands in Task 13; for this task, pass `cwd=self._cwd` (the existing global workspace) as a placeholder.

```python
    async def send(self, agent_name: str, message: str, *, task_id: int | None = None):
        role = self.registry.get(agent_name)
        client = await self._get_or_create_client(role.name, task_id=task_id, cwd=self._cwd)
        # ... existing send body unchanged ...
```

(e) Update `shutdown()` and `reset()` to handle the new key shape:

```python
    async def shutdown(self) -> None:
        async with self._lock:
            for key, client in list(self._clients.items()):
                try:
                    await client.disconnect()
                except Exception as exc:
                    log.warning("agent.shutdown_failed", key=key, error=str(exc))
            self._clients.clear()

    async def reset(self, agent_name: str, *, task_id: int | None = None) -> None:
        canonical = self.registry.resolve(agent_name)
        if canonical is None:
            raise KeyError(agent_name)
        async with self._lock:
            client = self._clients.pop((canonical, task_id), None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception as exc:
                log.warning("agent.reset_failed", agent=canonical, task_id=task_id, error=str(exc))
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_runner_pool.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Verify nothing else broke**

Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -5`
Expected: full suite still green.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/agents/runner.py tests/test_runner_pool.py
git commit -m "feat(runner): per-(agent, task) client pool + injectable factory hook"
```

---

## Task 13: Runner wires worktree lookup into send

**Files:**
- Modify: `agent_hub/agents/runner.py`
- Modify: `tests/test_runner_pool.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_runner_pool.py`:

```python
@pytest.mark.asyncio
async def test_send_uses_worktree_path_for_task(patched_runner, tmp_path):
    """When task_id is given AND a worktree is recorded for it, the runner
    should construct the client with cwd=that worktree path (not the
    global workspace)."""
    runner, created = patched_runner

    # Seed: init the DB and record a worktree for task_id=42.
    from agent_hub.db import Database
    from agent_hub.tasks.repository import TaskRepository
    from agent_hub.tasks.worktree_repo import WorktreeRepository

    db_path = runner.settings.database_path
    db = Database(db_path)
    await db.init()
    repo = TaskRepository(db_path)
    wt_repo = WorktreeRepository(db_path)
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    fake_wt_path = tmp_path / "worktrees" / str(task.id)
    fake_wt_path.mkdir(parents=True)
    await wt_repo.record(
        task_id=task.id, path=str(fake_wt_path),
        branch="task/x", base_branch="main",
    )

    client = await runner._get_or_create_client("pm", task_id=task.id, cwd=None)
    # Even though cwd=None was passed, the runner should have resolved
    # cwd via the worktree path for this task_id.
    assert client.options.cwd == str(fake_wt_path)
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_runner_pool.py::test_send_uses_worktree_path_for_task -v`
Expected: FAIL — runner doesn't look up worktree path.

- [ ] **Step 3: Add worktree resolution to `_get_or_create_client`**

In `agent_hub/agents/runner.py`, modify `_get_or_create_client`. Before the `options = build_sdk_options(...)` line, resolve the effective cwd:

```python
    async def _get_or_create_client(
        self,
        agent_name: str,
        *,
        task_id: int | None,
        cwd: Path | None,
    ) -> Any:
        from agent_hub.agents.runner_options import build_sdk_options
        from agent_hub.tasks.worktree_repo import WorktreeRepository

        role = self.registry.get(agent_name)
        key = (role.name, task_id)
        async with self._lock:
            if key in self._clients:
                return self._clients[key]

            # Resolve effective cwd: prefer recorded worktree for task_id,
            # fall back to caller-supplied cwd, then to global workspace.
            effective_cwd: Path | None = cwd
            if task_id is not None and effective_cwd is None:
                wt_repo = WorktreeRepository(self.settings.database_path)
                row = await wt_repo.get_by_task(task_id)
                if row is not None and row.cleaned_at is None:
                    effective_cwd = Path(row.path)
            if effective_cwd is None:
                effective_cwd = self._cwd

            options = build_sdk_options(
                role, cwd=effective_cwd, db_path=self.settings.database_path,
            )
            client = _client_factory(options)
            await client.connect()
            self._clients[key] = client
            log.info(
                "agent.started",
                agent=role.name,
                task_id=task_id,
                model=role.model,
                tools=role.allowed_tools,
                cwd=str(effective_cwd) if effective_cwd else None,
            )
            return client
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_runner_pool.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Full suite**

Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -5`
Expected: full suite green.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/agents/runner.py tests/test_runner_pool.py
git commit -m "feat(runner): resolve cwd from WorktreeRepository when task_id is given"
```

---

## Task 14: __main__ acquires the orchestrator lock

**Files:** Modify `agent_hub/__main__.py`; create `tests/test_main_lock.py`.

- [ ] **Step 1: Write a failing test**

Create `tests/test_main_lock.py`:

```python
"""Tests that the agent_hub entrypoint acquires the orchestrator lock
before doing any setup. We don't boot the whole bot — we exercise the
lock-acquisition helper directly."""

import os
from pathlib import Path

import pytest

from agent_hub.__main__ import _resolve_lock_path, _acquire_orchestrator_lock_or_exit
from agent_hub.orchestrator.lock import LockHeld


def test_resolve_lock_path_is_alongside_db(tmp_path: Path):
    db_path = tmp_path / "data" / "agent_hub.db"
    lock_path = _resolve_lock_path(db_path)
    assert lock_path == db_path.parent / ".orchestrator.lock"


def test_acquire_lock_succeeds_on_fresh_path(tmp_path: Path):
    db_path = tmp_path / "data" / "agent_hub.db"
    lock = _acquire_orchestrator_lock_or_exit(db_path)
    try:
        assert (tmp_path / "data" / ".orchestrator.lock").exists()
        assert (tmp_path / "data" / ".orchestrator.lock").read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_acquire_lock_raises_when_already_held(tmp_path: Path):
    db_path = tmp_path / "data" / "agent_hub.db"
    first = _acquire_orchestrator_lock_or_exit(db_path)
    try:
        with pytest.raises(LockHeld):
            _acquire_orchestrator_lock_or_exit(db_path)
    finally:
        first.release()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_main_lock.py -v`
Expected: ImportError — the two helpers don't exist yet.

- [ ] **Step 3: Add helpers and wire into main()**

Open `agent_hub/__main__.py`. Add at the top of the file:

```python
from agent_hub.orchestrator.lock import OrchestratorLock


def _resolve_lock_path(db_path: Path) -> Path:
    return db_path.parent / ".orchestrator.lock"


def _acquire_orchestrator_lock_or_exit(db_path: Path) -> OrchestratorLock:
    """Acquire the per-workspace orchestrator lock. Caller is responsible
    for releasing it on shutdown."""
    lock_path = _resolve_lock_path(db_path)
    lock = OrchestratorLock(lock_path)
    lock.acquire()
    return lock
```

In `main()`, after `_configure_logging(...)` but BEFORE `registry = AgentRegistry.load()`, add:

```python
    # Single-instance lock — refuses to start if another agent_hub is alive.
    lock = _acquire_orchestrator_lock_or_exit(settings.database_path)
```

And in the shutdown path (the existing `_post_shutdown` callback) — actually, the simplest robust pattern is to wrap the bot.run_polling in a try/finally:

Modify the bottom of `main()` to:

```python
    try:
        log.info("agent_hub.polling")
        app.run_polling(stop_signals=None) if sys.platform == "win32" else app.run_polling()
    finally:
        lock.release()
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_main_lock.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Verify nothing else broke**

Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -5`
Expected: full suite green.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/__main__.py tests/test_main_lock.py
git commit -m "feat(main): acquire orchestrator lock at startup; release on exit"
```

---

## Task 15: __main__ exports AGENT_HUB_DB for child MCP processes

**Files:** Modify `agent_hub/__main__.py`; modify `tests/test_main_lock.py`.

- [ ] **Step 1: Add failing test**

Append to `tests/test_main_lock.py`:

```python
def test_export_db_path_sets_env(tmp_path: Path, monkeypatch):
    """The runner's child MCP processes need AGENT_HUB_DB to find the
    database. The entrypoint exports it before launching anything."""
    from agent_hub.__main__ import _export_db_path_to_env

    db_path = tmp_path / "data" / "agent_hub.db"
    monkeypatch.delenv("AGENT_HUB_DB", raising=False)
    _export_db_path_to_env(db_path)
    assert os.environ["AGENT_HUB_DB"] == str(db_path)
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_main_lock.py::test_export_db_path_sets_env -v`
Expected: ImportError.

- [ ] **Step 3: Add helper + wire it**

In `agent_hub/__main__.py`, add:

```python
def _export_db_path_to_env(db_path: Path) -> None:
    """Make the absolute DB path visible to child processes (e.g. the
    per-agent MCP servers) that inherit our environment."""
    os.environ["AGENT_HUB_DB"] = str(db_path)
```

Call it in `main()` immediately after lock acquisition:

```python
    lock = _acquire_orchestrator_lock_or_exit(settings.database_path)
    _export_db_path_to_env(settings.database_path)
```

Add `import os` at the top of the file if not present.

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_main_lock.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/__main__.py tests/test_main_lock.py
git commit -m "feat(main): export AGENT_HUB_DB env var for child MCP processes"
```

---

## Task 16: WorktreeRepository — list_orphans (recorded but not on disk)

**Files:**
- Modify: `agent_hub/tasks/worktree_repo.py`
- Modify: `tests/test_worktree_repo.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_worktree_repo.py`:

```python
from pathlib import Path


@pytest.mark.asyncio
async def test_list_orphans_returns_rows_with_missing_dirs(deps, tmp_path):
    repo, worktrees = deps
    t1 = await repo.create(title="alive", description="-", origin_chat_id=1)
    t2 = await repo.create(title="orphan", description="-", origin_chat_id=1)

    alive_path = tmp_path / "alive"
    alive_path.mkdir()
    orphan_path = tmp_path / "orphan-does-not-exist"  # deliberately not created

    await worktrees.record(task_id=t1.id, path=str(alive_path), branch="task/1-alive", base_branch="main")
    await worktrees.record(task_id=t2.id, path=str(orphan_path), branch="task/2-orphan", base_branch="main")

    orphans = await worktrees.list_orphans()
    assert {o.task_id for o in orphans} == {t2.id}


@pytest.mark.asyncio
async def test_list_orphans_excludes_cleaned_rows(deps, tmp_path):
    repo, worktrees = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    missing = tmp_path / "nope"
    await worktrees.record(task_id=t.id, path=str(missing), branch="task/x", base_branch="main")
    await worktrees.mark_cleaned(t.id)

    orphans = await worktrees.list_orphans()
    assert orphans == []
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_repo.py -v`
Expected: 2 new FAIL.

- [ ] **Step 3: Implement list_orphans**

Append to `WorktreeRepository` in `agent_hub/tasks/worktree_repo.py`:

```python
    async def list_orphans(self) -> list[Worktree]:
        """Rows where cleaned_at IS NULL but the worktree path no longer
        exists on disk. The orchestrator (Plan 3) surfaces these to the
        user on boot — we never auto-delete."""
        active = await self.list_active()
        return [w for w in active if not Path(w.path).exists()]
```

Add `from pathlib import Path` at the top of the file if not present.

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_worktree_repo.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/tasks/worktree_repo.py tests/test_worktree_repo.py
git commit -m "feat(worktree-repo): add list_orphans (recorded but missing on disk)"
```

---

## Task 17: Full suite + parallel verification

**Files:** none (verification only).

- [ ] **Step 1: Serial run**

Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -10`
Expected: all pass. Tally roughly:
- Plan 1 baseline: 74
- New: lock (6) + worktree_repo (7) + worktree_manager (~13) + runner_options (4) + runner_pool (6) + main_lock (4) = **~40 new tests**
- Total: ~114 tests passing.

- [ ] **Step 2: Parallel run**

Run: `.\.venv\Scripts\python.exe -m pytest -n auto 2>&1 | tail -5`
Expected: same total, all pass.

- [ ] **Step 3: Inspect any flake**

If the worktree-manager tests flake under parallel execution (they spawn git subprocesses, real I/O), inspect which: likely a fixture that doesn't use unique `tmp_path` subdirectories. Fix the fixture, do not loosen the assertion.

- [ ] **Step 4: Commit only if a real isolation fix landed**

Otherwise, no commit needed.

---

## Self-review

**Spec coverage:**

- §4.4 AgentRunner updates — Tasks 11, 12, 13 ✓
- §4.6 Worktree manager — Tasks 7, 8, 9, 10 ✓
- §6.B single-orchestrator lock — Tasks 1–4 ✓
- §6.D orphan worktree detection — Task 16 ✓
- §6.D restart-in-flight scan — deferred to Plan 3 (orchestrator boot scan)

**Placeholder scan:** none. Every step has runnable code or an explicit command.

**Type consistency:**
- `WorktreeRepository` methods used consistently across tasks (`record`, `get_by_task`, `mark_cleaned`, `list_active`, `list_orphans`).
- Runner's `_get_or_create_client(agent_name, *, task_id, cwd)` keyword-only signature consistent across Tasks 12 and 13.
- `branch_slug(task_id, title)` signature stable in Task 7 and reused in Task 8.
- `_client_factory(options)` module-level hook introduced in Task 12 and used by the test in Task 13 via the same monkeypatch path.

**Known sequencing notes for the executor:**
- Task 11 introduces `build_sdk_options` / `build_mcp_server_config` — Tasks 12 and 13 import these.
- Task 12 changes `_clients` keying from `agent_name` to `(agent_name, task_id)`. Any other code path that touches `_clients` in `runner.py` must be updated in the same task (the original `runner.py` only references `_clients` inside `send`, `shutdown`, `reset`, `_get_client` — all four are rewritten in Task 12).
- Task 14 wraps `app.run_polling` in `try/finally` for lock release. The existing `_post_shutdown` callback can stay untouched — it handles SDK client teardown; the lock is a separate concern.
