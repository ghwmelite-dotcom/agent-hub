# Project Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a workspace-scoped shared memory store to agent-hub so agents accumulate project facts, lessons, preferences, and decisions across tasks — auto-captured at orchestrator hook points and injected into every agent's system prompt.

**Architecture:** New SQLite table `project_memory` keyed by workspace path. A `MemoryStore` class owns CRUD + prompt assembly. Capture is orchestrator-driven (no agent reliance) at five hook points. `build_sdk_options` appends a `## Project memory` block to each role's system prompt; a SHA-256 fingerprint stored on `agent_sessions` triggers a clean session re-attach when memory changes materially.

**Tech Stack:** Python 3.12, aiosqlite, pytest + pytest-asyncio (existing patterns), claude_agent_sdk (existing), python-telegram-bot (existing).

**Spec:** `docs/superpowers/specs/2026-05-20-project-memory-design.md`

---

## File Structure

**New files:**
- `agent_hub/memory/__init__.py` — package marker
- `agent_hub/memory/store.py` — `MemoryStore` class
- `agent_hub/memory/capture.py` — five capture hook functions
- `agent_hub/memory/preferences.py` — preference-marker regex + candidate state
- `agent_hub/mcp_server/tools/memory_tools.py` — `memory.note` MCP tool
- `agent_hub/telegram_bot/commands/memory_cmd.py` — `/memory`, `/forget`, `/remember`, `/memory clear`
- `tests/test_memory_store.py`
- `tests/test_memory_capture.py`
- `tests/test_memory_preferences.py`
- `tests/test_commands_memory.py`
- `tests/test_mcp_tools_memory.py`

**Modified files:**
- `agent_hub/db.py` — schema for `project_memory`, `_migrate_agent_sessions_fingerprint`
- `agent_hub/agents/session_store.py` — `get_fingerprint` / `set_fingerprint`
- `agent_hub/agents/runner_options.py` — `build_sdk_options` calls memory injection
- `agent_hub/agents/runner.py` — fingerprint compare → `session_store.forget` on mismatch
- `agent_hub/telegram_bot/commands/approve_cmd.py` — invoke `on_design_approved` capture
- `agent_hub/telegram_bot/commands/reject_cmd.py` — invoke `on_reject` capture
- `agent_hub/tasks/handoff_queue.py` — invoke `on_reviewer_kickback` / `on_qa_fail`
- `agent_hub/telegram_bot/bot.py` — wire preference-candidate detection + inline-keyboard handler
- `tests/test_runner_options.py` — extend with memory-injection assertions
- `tests/test_session_store.py` — extend with fingerprint accessor tests
- `tests/test_smoke.py` — assert a `decision` row is written end-to-end

---

## Task 1: DB schema migration

**Files:**
- Modify: `agent_hub/db.py`
- Test: `tests/test_schema.py` (extend existing)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schema.py`:

```python
@pytest.mark.asyncio
async def test_project_memory_table_exists(temp_db_path):
    from agent_hub.db import Database
    db = Database(temp_db_path)
    await db.init()
    import aiosqlite
    async with aiosqlite.connect(temp_db_path) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='project_memory'"
        )
        row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_project_memory_has_expected_columns(temp_db_path):
    from agent_hub.db import Database
    db = Database(temp_db_path)
    await db.init()
    import aiosqlite
    async with aiosqlite.connect(temp_db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(project_memory)")
        cols = {r[1] for r in await cur.fetchall()}
    assert cols == {
        "id", "workspace", "type", "agent_source", "title", "body",
        "related_task", "created_at", "last_used_at", "use_count", "archived",
    }


@pytest.mark.asyncio
async def test_agent_sessions_has_memory_fingerprint(temp_db_path):
    from agent_hub.db import Database
    db = Database(temp_db_path)
    await db.init()
    import aiosqlite
    async with aiosqlite.connect(temp_db_path) as conn:
        cur = await conn.execute("PRAGMA table_info(agent_sessions)")
        cols = {r[1] for r in await cur.fetchall()}
    assert "memory_fingerprint" in cols


@pytest.mark.asyncio
async def test_db_init_is_idempotent(temp_db_path):
    """Re-running init on an existing DB must not raise."""
    from agent_hub.db import Database
    db = Database(temp_db_path)
    await db.init()
    await db.init()  # second time — must be safe
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_schema.py -v -k "project_memory or fingerprint or idempotent"`
Expected: 4 failures (table missing, column missing).

- [ ] **Step 3: Add schema and migration in `agent_hub/db.py`**

Add this constant alongside the other `_SCHEMA_*` blocks:

```python
_SCHEMA_PROJECT_MEMORY = """
CREATE TABLE IF NOT EXISTS project_memory (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace     TEXT    NOT NULL,
    type          TEXT    NOT NULL CHECK (type IN (
                      'project_fact','lesson','preference','decision')),
    agent_source  TEXT,
    title         TEXT    NOT NULL,
    body          TEXT    NOT NULL,
    related_task  INTEGER REFERENCES tasks(id),
    created_at    TEXT    NOT NULL,
    last_used_at  TEXT,
    use_count     INTEGER NOT NULL DEFAULT 0,
    archived      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pm_workspace_type
    ON project_memory(workspace, type, archived);
CREATE INDEX IF NOT EXISTS idx_pm_last_used
    ON project_memory(workspace, last_used_at);
"""


async def _migrate_agent_sessions_fingerprint(conn: aiosqlite.Connection) -> None:
    """Idempotent: add `memory_fingerprint` column to `agent_sessions` if missing.

    Holds the SHA-256 of the assembled memory section last seen by this
    (agent, task) session. Mismatch on next connect → drop the session so
    the next SDK attach builds a fresh system prompt.
    """
    cur = await conn.execute("PRAGMA table_info(agent_sessions)")
    rows = await cur.fetchall()
    existing = {r[1] for r in rows}
    if "memory_fingerprint" not in existing:
        await conn.execute(
            "ALTER TABLE agent_sessions ADD COLUMN memory_fingerprint TEXT"
        )
```

Update the `init` method body to run both:

```python
await conn.executescript(_SCHEMA_AGENT_SESSIONS)
await conn.executescript(_SCHEMA_PROJECT_MEMORY)  # NEW
await _migrate_gates_notified_at(conn)
await _migrate_tasks_cost_total(conn)
await _migrate_agent_sessions_fingerprint(conn)  # NEW
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schema.py -v -k "project_memory or fingerprint or idempotent"`
Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/db.py tests/test_schema.py
git commit -m "feat(memory): add project_memory table + agent_sessions.memory_fingerprint"
```

---

## Task 2: MemoryStore — insert with dedupe

**Files:**
- Create: `agent_hub/memory/__init__.py`
- Create: `agent_hub/memory/store.py`
- Test: `tests/test_memory_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_store.py`:

```python
"""Tests for MemoryStore — CRUD, dedupe, load_for_prompt, fingerprint."""

from __future__ import annotations

import pytest

from agent_hub.db import Database
from agent_hub.memory.store import MemoryStore


@pytest.fixture
async def store(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return MemoryStore(temp_db_path)


@pytest.mark.asyncio
async def test_insert_returns_id(store):
    new_id = await store.insert(
        workspace=r"C:\dev\foo",
        type="lesson",
        agent_source="reviewer",
        title="Always await async handlers",
        body="Reviewer kicked back task #3 for unawaited promise.",
        related_task=3,
    )
    assert isinstance(new_id, int)
    assert new_id > 0


@pytest.mark.asyncio
async def test_insert_dedupes_on_title(store):
    """Two rows with same workspace+type+title collapse to one and bump use_count."""
    id1 = await store.insert(
        workspace=r"C:\dev\foo",
        type="lesson",
        agent_source="reviewer",
        title="Always await async handlers",
        body="First occurrence",
    )
    id2 = await store.insert(
        workspace=r"C:\dev\foo",
        type="lesson",
        agent_source="reviewer",
        title="Always await async handlers",
        body="Second occurrence — different body",
    )
    # Same row returned
    assert id1 == id2
    # use_count bumped
    rows = await store.list(workspace=r"C:\dev\foo", type="lesson")
    assert len(rows) == 1
    assert rows[0]["use_count"] == 1  # 0 → 1 on the dedupe hit
    # Original body preserved (we don't overwrite)
    assert rows[0]["body"] == "First occurrence"


@pytest.mark.asyncio
async def test_dedupe_is_workspace_scoped(store):
    """Same title in different workspace creates a separate row."""
    await store.insert(
        workspace=r"C:\dev\foo", type="lesson", agent_source="reviewer",
        title="X", body="b",
    )
    await store.insert(
        workspace=r"C:\dev\bar", type="lesson", agent_source="reviewer",
        title="X", body="b",
    )
    foo_rows = await store.list(workspace=r"C:\dev\foo", type="lesson")
    bar_rows = await store.list(workspace=r"C:\dev\bar", type="lesson")
    assert len(foo_rows) == 1
    assert len(bar_rows) == 1
    assert foo_rows[0]["id"] != bar_rows[0]["id"]


@pytest.mark.asyncio
async def test_list_excludes_archived(store):
    new_id = await store.insert(
        workspace=r"C:\dev\foo", type="lesson", agent_source="reviewer",
        title="X", body="b",
    )
    await store.archive(new_id)
    rows = await store.list(workspace=r"C:\dev\foo", type="lesson")
    assert rows == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_store.py -v`
Expected: failures — `agent_hub.memory.store` does not exist.

- [ ] **Step 3: Create the package and `MemoryStore`**

Create `agent_hub/memory/__init__.py` (empty file).

Create `agent_hub/memory/store.py`:

```python
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
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_memory_store.py -v`
Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/memory/ tests/test_memory_store.py
git commit -m "feat(memory): MemoryStore.insert/list/archive with title-based dedupe"
```

---

## Task 3: MemoryStore.load_for_prompt with selection rules

**Files:**
- Modify: `agent_hub/memory/store.py`
- Test: `tests/test_memory_store.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_memory_store.py`:

```python
@pytest.mark.asyncio
async def test_load_for_prompt_returns_empty_when_no_memory(store):
    section = await store.load_for_prompt(
        workspace=r"C:\dev\foo", agent_name="fullstack-engineer",
    )
    assert section == ""


@pytest.mark.asyncio
async def test_load_for_prompt_includes_all_types_for_pm(store):
    ws = r"C:\dev\foo"
    await store.insert(workspace=ws, type="project_fact", agent_source="architect",
                       title="Stack: Workers + D1", body="No Postgres")
    await store.insert(workspace=ws, type="preference", agent_source="user",
                       title="Don't add code comments", body="explicit user pref")
    await store.insert(workspace=ws, type="lesson", agent_source="reviewer",
                       title="Always await handlers", body="task #3 kickback")
    await store.insert(workspace=ws, type="decision", agent_source="architect",
                       title="Use Drizzle ORM", body="type safety with D1")
    section = await store.load_for_prompt(workspace=ws, agent_name="pm")
    assert "## Project memory" in section
    assert "Stack: Workers + D1" in section
    assert "Don't add code comments" in section
    assert "Always await handlers" in section
    assert "Use Drizzle ORM" in section


@pytest.mark.asyncio
async def test_load_for_prompt_qa_skips_preferences_and_decisions(store):
    """Per-role filtering: qa sees facts + lessons only."""
    ws = r"C:\dev\foo"
    await store.insert(workspace=ws, type="project_fact", agent_source="architect",
                       title="FACT-X", body="b")
    await store.insert(workspace=ws, type="preference", agent_source="user",
                       title="PREF-X", body="b")
    await store.insert(workspace=ws, type="lesson", agent_source="reviewer",
                       title="LESSON-X", body="b")
    await store.insert(workspace=ws, type="decision", agent_source="architect",
                       title="DECISION-X", body="b")
    section = await store.load_for_prompt(workspace=ws, agent_name="qa")
    assert "FACT-X" in section
    assert "LESSON-X" in section
    assert "PREF-X" not in section
    assert "DECISION-X" not in section


@pytest.mark.asyncio
async def test_load_for_prompt_caps_lessons_to_five(store):
    """Only the 5 most recent lessons are included; older drop out."""
    ws = r"C:\dev\foo"
    for i in range(7):
        await store.insert(
            workspace=ws, type="lesson", agent_source="reviewer",
            title=f"Lesson {i}", body=f"b{i}",
        )
    section = await store.load_for_prompt(workspace=ws, agent_name="pm")
    assert section.count("Lesson ") == 5
    # Newest (6) present, oldest (0) absent
    assert "Lesson 6" in section
    assert "Lesson 0" not in section


@pytest.mark.asyncio
async def test_load_for_prompt_bumps_use_count_for_included(store):
    ws = r"C:\dev\foo"
    new_id = await store.insert(
        workspace=ws, type="lesson", agent_source="reviewer",
        title="X", body="b",
    )
    await store.load_for_prompt(workspace=ws, agent_name="pm")
    rows = await store.list(workspace=ws, type="lesson")
    assert rows[0]["use_count"] == 1
    assert rows[0]["last_used_at"] is not None


@pytest.mark.asyncio
async def test_load_for_prompt_unknown_agent_falls_back_to_all_types(store):
    """Defensive: an unknown role gets a sane default rather than empty."""
    ws = r"C:\dev\foo"
    await store.insert(workspace=ws, type="project_fact", agent_source="x",
                       title="FACT", body="b")
    section = await store.load_for_prompt(workspace=ws, agent_name="brand-new-role")
    assert "FACT" in section
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_store.py -v -k "load_for_prompt"`
Expected: 6 failures — `load_for_prompt` not defined.

- [ ] **Step 3: Add `load_for_prompt` and role mapping in `agent_hub/memory/store.py`**

Add to `agent_hub/memory/store.py` (top, after `_VALID_TYPES`):

```python
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
```

Add the method to the `MemoryStore` class:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_store.py -v`
Expected: all pass (10 total).

- [ ] **Step 5: Commit**

```bash
git add agent_hub/memory/store.py tests/test_memory_store.py
git commit -m "feat(memory): load_for_prompt with per-role filtering + bookkeeping"
```

---

## Task 4: MemoryStore — size cap + fingerprint

**Files:**
- Modify: `agent_hub/memory/store.py`
- Test: `tests/test_memory_store.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_memory_store.py`:

```python
@pytest.mark.asyncio
async def test_load_for_prompt_enforces_size_cap(store):
    """When section exceeds the cap, lessons drop first, then decisions.
    Facts and preferences are never dropped."""
    ws = r"C:\dev\foo"
    # Big titles to blow the cap quickly. Cap is ~2000 tokens ≈ 8000 chars.
    big = "X" * 1000
    for i in range(4):
        await store.insert(workspace=ws, type="lesson", agent_source="reviewer",
                           title=f"L{i} {big}", body="b")
    for i in range(4):
        await store.insert(workspace=ws, type="decision", agent_source="architect",
                           title=f"D{i} {big}", body="b")
    await store.insert(workspace=ws, type="project_fact", agent_source="architect",
                       title=f"FACT {big}", body="b")
    section = await store.load_for_prompt(workspace=ws, agent_name="pm")
    # Fact survives
    assert "FACT" in section
    # Section is under the byte cap
    assert len(section) <= 8000
    # At least one lesson was dropped
    assert section.count("L") < 4 + 4 + 1  # not all 9 entries fit


@pytest.mark.asyncio
async def test_fingerprint_stable_for_same_inputs(store):
    ws = r"C:\dev\foo"
    await store.insert(workspace=ws, type="project_fact", agent_source="architect",
                       title="X", body="b")
    fp1 = await store.fingerprint(workspace=ws, agent_name="pm")
    fp2 = await store.fingerprint(workspace=ws, agent_name="pm")
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex


@pytest.mark.asyncio
async def test_fingerprint_changes_after_insert(store):
    ws = r"C:\dev\foo"
    fp1 = await store.fingerprint(workspace=ws, agent_name="pm")
    await store.insert(workspace=ws, type="project_fact", agent_source="architect",
                       title="X", body="b")
    fp2 = await store.fingerprint(workspace=ws, agent_name="pm")
    assert fp1 != fp2


@pytest.mark.asyncio
async def test_fingerprint_different_for_different_roles(store):
    """Different per-role filtering → different fingerprint."""
    ws = r"C:\dev\foo"
    await store.insert(workspace=ws, type="decision", agent_source="architect",
                       title="X", body="b")
    fp_pm = await store.fingerprint(workspace=ws, agent_name="pm")  # sees decision
    fp_qa = await store.fingerprint(workspace=ws, agent_name="qa")  # does not
    assert fp_pm != fp_qa


@pytest.mark.asyncio
async def test_fingerprint_does_not_bump_use_count(store):
    """fingerprint() is a read-only helper — must not mutate."""
    ws = r"C:\dev\foo"
    new_id = await store.insert(
        workspace=ws, type="lesson", agent_source="reviewer",
        title="X", body="b",
    )
    await store.fingerprint(workspace=ws, agent_name="pm")
    rows = await store.list(workspace=ws, type="lesson")
    assert rows[0]["use_count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_store.py -v -k "size_cap or fingerprint"`
Expected: 5 failures (cap not enforced, fingerprint missing).

- [ ] **Step 3: Add size-cap + fingerprint logic to `agent_hub/memory/store.py`**

Add to the top of `agent_hub/memory/store.py`:

```python
import hashlib

_MEMORY_BYTE_CAP = 8000  # ~2000 tokens at ~4 chars/token
```

Refactor `load_for_prompt` — extract the rendering into a helper and add a size-cap loop. Replace the existing `load_for_prompt` body's render-and-bookkeeping section with the following (everything from `# Render` down):

```python
            # Render with size cap. Drop lessons first, then decisions if
            # over the byte cap. Facts and preferences are never dropped.
            droppable_order = ("lesson", "decision")
            while True:
                section, included_ids = self._render_section(
                    workspace, entries_by_type,
                )
                if len(section) <= _MEMORY_BYTE_CAP:
                    break
                dropped = False
                for t in droppable_order:
                    if entries_by_type.get(t):
                        entries_by_type[t].pop()  # drop oldest of that type
                        dropped = True
                        break
                if not dropped:
                    break  # can't shrink further; let it through

            # Bookkeeping
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
```

Add the private renderer as a method on `MemoryStore`:

```python
    def _render_section(
        self,
        workspace: str,
        entries_by_type: dict[str, list[dict]],
    ) -> tuple[str, list[int]]:
        """Return (rendered_section, included_ids)."""
        if not any(entries_by_type.values()):
            return "", []
        lines = [f"## Project memory — {workspace}", ""]
        included_ids: list[int] = []
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
                included_ids.append(int(row["id"]))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n", included_ids
```

Note: `entries_by_type[t].pop()` removes the **oldest** entry. With the current `ORDER BY id DESC LIMIT` the list is newest-first, so `pop()` (default last) drops the oldest. That's the intended decay direction.

Add the `fingerprint` method to `MemoryStore`:

```python
    async def fingerprint(
        self,
        *,
        workspace: str,
        agent_name: str,
    ) -> str:
        """SHA-256 hex of the assembled section for (workspace, agent).

        Read-only: does NOT bump use_count or last_used_at. Used by the
        runner to detect that memory has changed since the SDK session
        was last attached, so we can drop the session and rebuild the
        system prompt.
        """
        allowed = _ROLE_TYPE_ALLOWLIST.get(agent_name, set(_VALID_TYPES))
        async with self._connect() as conn:
            conn.row_factory = aiosqlite.Row
            entries_by_type: dict[str, list[dict]] = {}
            for t in _TYPE_ORDER:
                if t not in allowed:
                    continue
                limit = _TYPE_LIMITS[t]
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

        section, _ = self._render_section(workspace, entries_by_type)
        return hashlib.sha256(section.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_store.py -v`
Expected: all pass (15 total).

- [ ] **Step 5: Commit**

```bash
git add agent_hub/memory/store.py tests/test_memory_store.py
git commit -m "feat(memory): size-cap + fingerprint for prompt assembly"
```

---

## Task 5: AgentSessionStore — fingerprint accessors

**Files:**
- Modify: `agent_hub/agents/session_store.py`
- Test: `tests/test_session_store.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_session_store.py`:

```python
@pytest.mark.asyncio
async def test_get_fingerprint_returns_none_when_unset(store):
    await store.get_or_create(agent_name="pm", task_id=1)
    fp = await store.get_fingerprint(agent_name="pm", task_id=1)
    assert fp is None


@pytest.mark.asyncio
async def test_set_and_get_fingerprint(store):
    await store.get_or_create(agent_name="pm", task_id=1)
    await store.set_fingerprint(
        agent_name="pm", task_id=1, fingerprint="abc123",
    )
    fp = await store.get_fingerprint(agent_name="pm", task_id=1)
    assert fp == "abc123"


@pytest.mark.asyncio
async def test_set_fingerprint_creates_row_if_missing(store):
    """No session created yet — set_fingerprint should still work (upsert)."""
    await store.set_fingerprint(
        agent_name="pm", task_id=99, fingerprint="zzz",
    )
    fp = await store.get_fingerprint(agent_name="pm", task_id=99)
    assert fp == "zzz"


@pytest.mark.asyncio
async def test_forget_clears_fingerprint(store):
    await store.get_or_create(agent_name="pm", task_id=1)
    await store.set_fingerprint(agent_name="pm", task_id=1, fingerprint="x")
    await store.forget(agent_name="pm", task_id=1)
    assert await store.get_fingerprint(agent_name="pm", task_id=1) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_session_store.py -v -k "fingerprint"`
Expected: 4 failures — methods don't exist.

- [ ] **Step 3: Add accessors to `agent_hub/agents/session_store.py`**

Add inside the `AgentSessionStore` class:

```python
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_session_store.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/agents/session_store.py tests/test_session_store.py
git commit -m "feat(memory): AgentSessionStore.get_fingerprint/set_fingerprint"
```

---

## Task 6: Inject memory into system prompt + fingerprint mismatch handling

**Files:**
- Modify: `agent_hub/agents/runner_options.py`
- Modify: `agent_hub/agents/runner.py`
- Test: `tests/test_runner_options.py` (extend) + new test for mismatch handling

- [ ] **Step 1: Write the failing test for `build_sdk_options`**

Append to `tests/test_runner_options.py`:

```python
@pytest.mark.asyncio
async def test_build_sdk_options_appends_memory_section(tmp_path, monkeypatch):
    """When memory exists for the workspace+role, it's appended to system_prompt."""
    from agent_hub.db import Database
    from agent_hub.memory.store import MemoryStore
    from agent_hub.agents.registry import AgentRole
    from agent_hub.agents.runner_options import build_sdk_options

    # Fake the SDK import to capture kwargs without spawning anything.
    captured = {}
    class _FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeAgentOptions", _FakeOptions, raising=False,
    )

    db_path = tmp_path / "agent_hub.db"
    db = Database(db_path)
    await db.init()

    ws = tmp_path / "workspace"
    ws.mkdir()
    store = MemoryStore(db_path)
    await store.insert(
        workspace=str(ws), type="project_fact", agent_source="architect",
        title="Stack is Workers + D1", body="b",
    )

    role = AgentRole(
        name="pm", display_name="PM", aliases=[], model="haiku",
        allowed_tools=["Read"], system_prompt="BASE PROMPT",
    )

    await build_sdk_options(role, cwd=ws, db_path=db_path, session_id=None)

    sp = captured["system_prompt"]
    assert "BASE PROMPT" in sp
    assert "## Project memory" in sp
    assert "Stack is Workers + D1" in sp


@pytest.mark.asyncio
async def test_build_sdk_options_no_memory_section_when_empty(tmp_path, monkeypatch):
    from agent_hub.db import Database
    from agent_hub.agents.registry import AgentRole
    from agent_hub.agents.runner_options import build_sdk_options

    captured = {}
    class _FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeAgentOptions", _FakeOptions, raising=False,
    )

    db_path = tmp_path / "agent_hub.db"
    db = Database(db_path)
    await db.init()
    ws = tmp_path / "workspace"
    ws.mkdir()

    role = AgentRole(
        name="pm", display_name="PM", aliases=[], model="haiku",
        allowed_tools=["Read"], system_prompt="BASE PROMPT",
    )
    await build_sdk_options(role, cwd=ws, db_path=db_path, session_id=None)
    assert captured["system_prompt"] == "BASE PROMPT"


@pytest.mark.asyncio
async def test_build_sdk_options_no_cwd_skips_memory(tmp_path, monkeypatch):
    """No workspace → no memory injection (memory is workspace-scoped)."""
    from agent_hub.db import Database
    from agent_hub.memory.store import MemoryStore
    from agent_hub.agents.registry import AgentRole
    from agent_hub.agents.runner_options import build_sdk_options

    captured = {}
    class _FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeAgentOptions", _FakeOptions, raising=False,
    )

    db_path = tmp_path / "agent_hub.db"
    db = Database(db_path)
    await db.init()
    # Insert memory under SOME workspace
    await MemoryStore(db_path).insert(
        workspace=r"C:\anywhere", type="project_fact", agent_source="x",
        title="X", body="b",
    )

    role = AgentRole(
        name="pm", display_name="PM", aliases=[], model="haiku",
        allowed_tools=["Read"], system_prompt="BASE PROMPT",
    )
    await build_sdk_options(role, cwd=None, db_path=db_path, session_id=None)
    assert "Project memory" not in captured["system_prompt"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runner_options.py -v -k "memory"`
Expected: failures — function is sync and doesn't load memory.

- [ ] **Step 3: Make `build_sdk_options` async + load memory**

Replace the existing `build_sdk_options` in `agent_hub/agents/runner_options.py` with:

```python
async def build_sdk_options(
    role: AgentRole,
    *,
    cwd: Path | None,
    db_path: Path,
    session_id: str | None = None,
) -> Any:
    """Construct a ClaudeAgentOptions for the given role + workspace.

    If `cwd` is set, loads project memory for that workspace+role and
    appends a `## Project memory` section to the role's system prompt.

    `session_id` (when set) pins the conversation to a known UUID so a
    later reconnect can pick up where it left off — the Claude Code CLI
    persists conversation history per session_id. Pass the value
    returned by AgentSessionStore.get_or_create.
    """
    import claude_agent_sdk as sdk

    system_prompt = role.system_prompt
    if cwd is not None:
        from agent_hub.memory.store import MemoryStore

        memory_section = await MemoryStore(db_path).load_for_prompt(
            workspace=str(cwd), agent_name=role.name,
        )
        if memory_section:
            system_prompt = f"{system_prompt}\n\n{memory_section}"

    builtin_tools = [t for t in role.allowed_tools if not t.startswith("mcp__")]
    kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "tools": builtin_tools,
        "allowed_tools": role.allowed_tools,
        "setting_sources": [],
        "skills": [],
        "model": role.model,
        "cwd": str(cwd) if cwd else None,
        "mcp_servers": build_mcp_server_config(db_path),
    }
    if session_id is not None:
        kwargs["session_id"] = session_id
    return sdk.ClaudeAgentOptions(**kwargs)
```

- [ ] **Step 4: Update `agent_hub/agents/runner.py` to await build_sdk_options + handle fingerprint mismatch**

In `_get_or_create_client`, replace the block from `# Persistent (agent, task_id) → SDK session UUID.` down to `options = build_sdk_options(...)` with:

```python
            # Fingerprint-based session refresh: if memory has changed
            # since this session was last attached, drop the session UUID
            # so the SDK rebuilds the system prompt with current memory.
            from agent_hub.memory.store import MemoryStore
            session_store = AgentSessionStore(self.settings.database_path)
            if effective_cwd is not None:
                current_fp = await MemoryStore(self.settings.database_path).fingerprint(
                    workspace=str(effective_cwd), agent_name=role.name,
                )
                stored_fp = await session_store.get_fingerprint(
                    agent_name=role.name, task_id=task_id,
                )
                if stored_fp is not None and stored_fp != current_fp:
                    await session_store.forget(
                        agent_name=role.name, task_id=task_id,
                    )
                await session_store.set_fingerprint(
                    agent_name=role.name, task_id=task_id,
                    fingerprint=current_fp,
                )

            session_id = await session_store.get_or_create(
                agent_name=role.name, task_id=task_id,
            )

            options = await build_sdk_options(
                role,
                cwd=effective_cwd,
                db_path=self.settings.database_path,
                session_id=session_id,
            )
```

- [ ] **Step 5: Add fingerprint-mismatch test**

Append to `tests/test_runner_options.py`:

```python
@pytest.mark.asyncio
async def test_fingerprint_mismatch_drops_session(tmp_path, monkeypatch):
    """If stored fingerprint differs from current, session_store.forget is called."""
    from agent_hub.db import Database
    from agent_hub.memory.store import MemoryStore
    from agent_hub.agents.session_store import AgentSessionStore
    from agent_hub.agents.runner import AgentRunner
    from agent_hub.agents.registry import AgentRegistry, AgentRole
    from agent_hub.config import Settings

    monkeypatch.setattr(
        "agent_hub.agents.runner._client_factory",
        lambda options: type("FakeClient", (), {
            "connect": lambda self: None,
        })(),
    )
    # Skip real sdk import inside build_sdk_options
    class _FakeOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
    monkeypatch.setattr(
        "claude_agent_sdk.ClaudeAgentOptions", _FakeOptions, raising=False,
    )

    db_path = tmp_path / "agent_hub.db"
    db = Database(db_path)
    await db.init()
    ws = tmp_path / "workspace"
    ws.mkdir()

    role = AgentRole(
        name="pm", display_name="PM", aliases=[], model="haiku",
        allowed_tools=["Read"], system_prompt="BASE",
    )
    registry = AgentRegistry([role])
    settings = Settings(
        database_path=db_path,
        default_workspace=ws,
    )

    runner = AgentRunner(settings, registry)

    # Prime: store a stale fingerprint that won't match current (no memory).
    session_store = AgentSessionStore(db_path)
    await session_store.set_fingerprint(
        agent_name="pm", task_id=1, fingerprint="STALE",
    )
    primed_session = await session_store.get_or_create(
        agent_name="pm", task_id=1,
    )

    # Now insert a memory row — current fingerprint will differ from "STALE".
    await MemoryStore(db_path).insert(
        workspace=str(ws), type="project_fact", agent_source="architect",
        title="X", body="b",
    )

    # Triggers fingerprint compare → forget → fresh UUID.
    client = await runner._get_or_create_client(
        "pm", task_id=1, cwd=ws,
    )
    new_session = await session_store.get(agent_name="pm", task_id=1)
    assert new_session != primed_session
```

Adjust your local `Settings` import path if it differs — the spec assumes `Settings(database_path=..., default_workspace=...)`. If the actual signature requires more fields, instantiate with whatever the real constructor needs; the assertion is on `session_store.get()` after the call.

- [ ] **Step 6: Run all tests to verify pass**

Run: `pytest tests/test_runner_options.py tests/test_memory_store.py tests/test_session_store.py -v`
Expected: all pass. There will be deprecation warnings from `_FakeOptions` — ignore.

- [ ] **Step 7: Commit**

```bash
git add agent_hub/agents/runner_options.py agent_hub/agents/runner.py \
    tests/test_runner_options.py
git commit -m "feat(memory): inject memory into system prompt + fingerprint refresh"
```

---

## Task 7: Capture hook on /approve (decision)

**Files:**
- Create: `agent_hub/memory/capture.py`
- Modify: `agent_hub/telegram_bot/commands/approve_cmd.py`
- Test: `tests/test_memory_capture.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_capture.py`:

```python
"""Tests for memory capture hooks — auto-write into project_memory at events."""

from __future__ import annotations

import pytest

from agent_hub.db import Database
from agent_hub.memory.capture import on_design_approved
from agent_hub.memory.store import MemoryStore


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return temp_db_path


@pytest.mark.asyncio
async def test_on_design_approved_writes_decision(db_path):
    await on_design_approved(
        db_path=db_path,
        workspace=r"C:\dev\foo",
        task_id=42,
        task_title="Add user signup",
        design_text="Use Auth0 + magic links. Reasoning: ...",
        agent_name="architect",
    )
    rows = await MemoryStore(db_path).list(
        workspace=r"C:\dev\foo", type="decision",
    )
    assert len(rows) == 1
    assert rows[0]["title"] == "Task #42: Add user signup"
    assert "Auth0" in rows[0]["body"]
    assert rows[0]["agent_source"] == "architect"
    assert rows[0]["related_task"] == 42


@pytest.mark.asyncio
async def test_on_design_approved_no_workspace_is_noop(db_path):
    """No workspace (e.g., user hasn't set one) → silently skip, don't crash."""
    await on_design_approved(
        db_path=db_path,
        workspace=None,
        task_id=42,
        task_title="t",
        design_text="d",
        agent_name="architect",
    )
    # No assertion on rows count by workspace=None — table is just empty.
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_memory_capture.py -v`
Expected: failures — `agent_hub.memory.capture` does not exist.

- [ ] **Step 3: Create `agent_hub/memory/capture.py`**

```python
"""Auto-capture hooks. Called by the orchestrator at key events.

Memory writes never raise — failures are logged and swallowed. The orchestrator
is the source of truth for the actual task flow; missing memory is a degraded
experience but not a broken pipeline.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from agent_hub.memory.store import MemoryStore

log = structlog.get_logger(__name__)


async def on_design_approved(
    *,
    db_path: Path,
    workspace: str | None,
    task_id: int,
    task_title: str,
    design_text: str,
    agent_name: str,
) -> None:
    """Called from approve_cmd after the gate is resolved."""
    if not workspace:
        return
    try:
        await MemoryStore(db_path).insert(
            workspace=workspace,
            type="decision",
            agent_source=agent_name,
            title=f"Task #{task_id}: {task_title}",
            body=design_text,
            related_task=task_id,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "memory.capture.on_design_approved.failed",
            task_id=task_id, workspace=workspace,
        )
```

- [ ] **Step 4: Wire `on_design_approved` into approve_cmd**

In `agent_hub/telegram_bot/commands/approve_cmd.py`:

Look at the existing signature of `handle_approve`. It currently takes `task_id`, `db_path`, `repo_root`, `worktrees_root`. We need the workspace path (which is `repo_root`) and the design text.

The design text comes from the architect's comment on the task. Look up the existing `TaskRepository` API — there is a `repo.comment(task_id, actor, body)` writer; we need a reader. If a reader doesn't exist, this needs a small addition.

Verify what the comment reader looks like:

Run: `pytest -q --collect-only tests/test_tasks_repository.py 2>&1 | head -40`

Then read `agent_hub/tasks/repository.py` for comment-reading APIs. Two cases:

**Case A — there's an existing comments reader (e.g., `repo.list_comments(task_id)` or `repo.recent_events`):** Use it to fetch the latest architect/quant comment and pass its body as `design_text`.

**Case B — no reader exists:** Add a minimal one to `TaskRepository`:

```python
    async def latest_comment_by(
        self, task_id: int, actor: str,
    ) -> str | None:
        """Return the most recent comment body where the actor matches.

        Used by the memory capture hook to grab the architect/quant
        design text at /approve time.
        """
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT payload_json FROM task_events "
                "WHERE task_id = ? AND actor = ? AND kind = 'comment' "
                "ORDER BY id DESC LIMIT 1",
                (task_id, actor),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        import json
        try:
            return json.loads(row["payload_json"]).get("body")
        except (ValueError, TypeError):
            return None
```

This assumes the existing `repo.comment(...)` writes to `task_events` with `kind='comment'` and `payload_json={"body": ...}`. If the actual schema differs, adjust the SELECT accordingly — verify by reading the existing `repo.comment` implementation before adding this method.

In `handle_approve`, after `await gates.resolve(...)` and before the return statements, add:

```python
    # Capture the design as a decision-log entry.
    from agent_hub.memory.capture import on_design_approved

    design_text = (
        await repo.latest_comment_by(task_id, "architect")
        or await repo.latest_comment_by(task_id, "quant")
        or ""
    )
    if design_text:
        await on_design_approved(
            db_path=db_path,
            workspace=str(repo_root) if repo_root else None,
            task_id=task_id,
            task_title=task.title,
            design_text=design_text,
            agent_name=(
                "quant"
                if await repo.latest_comment_by(task_id, "quant") else "architect"
            ),
        )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_memory_capture.py tests/test_commands_approve.py -v`
Expected: capture tests pass; approve command tests still pass (no behavioral regressions — the capture is opt-in on having repo_root).

- [ ] **Step 6: Commit**

```bash
git add agent_hub/memory/capture.py \
    agent_hub/telegram_bot/commands/approve_cmd.py \
    agent_hub/tasks/repository.py \
    tests/test_memory_capture.py
git commit -m "feat(memory): capture decisions on /approve"
```

---

## Task 8: Capture hook on /reject (lesson)

**Files:**
- Modify: `agent_hub/memory/capture.py`
- Modify: `agent_hub/telegram_bot/commands/reject_cmd.py`
- Test: `tests/test_memory_capture.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_memory_capture.py`:

```python
@pytest.mark.asyncio
async def test_on_reject_writes_lesson(db_path):
    from agent_hub.memory.capture import on_reject
    await on_reject(
        db_path=db_path,
        workspace=r"C:\dev\foo",
        task_id=7,
        task_title="Build payments form",
        reason="Doesn't handle Stripe webhook retries",
    )
    rows = await MemoryStore(db_path).list(
        workspace=r"C:\dev\foo", type="lesson",
    )
    assert len(rows) == 1
    assert rows[0]["title"] == "Rejected task #7: Build payments form"
    assert "Stripe webhook retries" in rows[0]["body"]
    assert rows[0]["agent_source"] == "user"
    assert rows[0]["related_task"] == 7


@pytest.mark.asyncio
async def test_on_reject_no_workspace_is_noop(db_path):
    from agent_hub.memory.capture import on_reject
    await on_reject(
        db_path=db_path, workspace=None,
        task_id=7, task_title="t", reason="r",
    )
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_memory_capture.py -v -k "reject"`
Expected: failures.

- [ ] **Step 3: Add `on_reject` to `agent_hub/memory/capture.py`**

```python
async def on_reject(
    *,
    db_path: Path,
    workspace: str | None,
    task_id: int,
    task_title: str,
    reason: str,
) -> None:
    """Called from reject_cmd after the gate is rejected."""
    if not workspace:
        return
    try:
        await MemoryStore(db_path).insert(
            workspace=workspace,
            type="lesson",
            agent_source="user",
            title=f"Rejected task #{task_id}: {task_title}",
            body=reason,
            related_task=task_id,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "memory.capture.on_reject.failed",
            task_id=task_id, workspace=workspace,
        )
```

- [ ] **Step 4: Wire into `handle_reject`**

`handle_reject` currently does NOT take a workspace argument. Add `workspace: str | None = None` to its signature (default kept for backward compatibility with existing tests). The bot caller (in `agent_hub/telegram_bot/bot.py`) needs to pass the active workspace string.

In `reject_cmd.py`:

```python
async def handle_reject(
    *,
    task_id: int,
    reason: str,
    db_path: Path,
    workspace: str | None = None,
) -> str:
    # ... existing body unchanged ...
    # After the existing `await repo.comment(task_id, actor="user", body=f"Rejected: {reason}")` line:

    from agent_hub.memory.capture import on_reject
    await on_reject(
        db_path=db_path,
        workspace=workspace,
        task_id=task_id,
        task_title=task.title,
        reason=reason,
    )

    # ... rest unchanged ...
```

In `agent_hub/telegram_bot/bot.py`, find the `/reject` handler that calls `handle_reject(...)` and pass the current workspace string. Grep for `handle_reject` to locate it:

Run: `grep -n "handle_reject" agent_hub/telegram_bot/bot.py`

Update the call site to `await handle_reject(task_id=..., reason=..., db_path=..., workspace=str(self.runner.workspace) if self.runner.workspace else None)`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_memory_capture.py tests/test_commands_reject.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/memory/capture.py \
    agent_hub/telegram_bot/commands/reject_cmd.py \
    agent_hub/telegram_bot/bot.py \
    tests/test_memory_capture.py
git commit -m "feat(memory): capture lessons on /reject"
```

---

## Task 9: Capture hooks on reviewer-kickback / qa-fail

**Files:**
- Modify: `agent_hub/memory/capture.py`
- Modify: `agent_hub/tasks/handoff_queue.py`
- Test: `tests/test_memory_capture.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_memory_capture.py`:

```python
@pytest.mark.asyncio
async def test_reviewer_kickback_writes_lesson(db_path):
    """from=reviewer, to=fullstack-engineer is interpreted as a kickback."""
    from agent_hub.memory.capture import on_handoff_kickback
    await on_handoff_kickback(
        db_path=db_path,
        workspace=r"C:\dev\foo",
        task_id=11,
        from_agent="reviewer",
        to_agent="fullstack-engineer",
        message="Unawaited promise in handlers.ts — please add `await`.",
    )
    rows = await MemoryStore(db_path).list(
        workspace=r"C:\dev\foo", type="lesson",
    )
    assert len(rows) == 1
    assert rows[0]["agent_source"] == "reviewer"
    assert "Unawaited promise" in rows[0]["body"]
    assert rows[0]["related_task"] == 11


@pytest.mark.asyncio
async def test_qa_fail_writes_lesson(db_path):
    from agent_hub.memory.capture import on_handoff_kickback
    await on_handoff_kickback(
        db_path=db_path,
        workspace=r"C:\dev\foo",
        task_id=11,
        from_agent="qa",
        to_agent="fullstack-engineer",
        message="Failing test: tests/test_x.py::test_y",
    )
    rows = await MemoryStore(db_path).list(
        workspace=r"C:\dev\foo", type="lesson",
    )
    assert rows[0]["agent_source"] == "qa"


@pytest.mark.asyncio
async def test_normal_forward_handoff_does_not_capture(db_path):
    """fullstack → reviewer is normal forward progress — no lesson."""
    from agent_hub.memory.capture import on_handoff_kickback
    await on_handoff_kickback(
        db_path=db_path,
        workspace=r"C:\dev\foo",
        task_id=11,
        from_agent="fullstack-engineer",
        to_agent="reviewer",
        message="Done, ready for review",
    )
    rows = await MemoryStore(db_path).list(
        workspace=r"C:\dev\foo", type="lesson",
    )
    assert rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_capture.py -v -k "kickback or qa_fail or normal_forward"`
Expected: 3 failures.

- [ ] **Step 3: Add `on_handoff_kickback` to `agent_hub/memory/capture.py`**

```python
# Direction pairs that count as a "kickback" (reverse-flow handoff).
_KICKBACK_PAIRS = {
    ("reviewer", "fullstack-engineer"),
    ("reviewer", "implementer"),
    ("qa", "fullstack-engineer"),
    ("qa", "implementer"),
    ("backtest-analyst", "fullstack-engineer"),
}


async def on_handoff_kickback(
    *,
    db_path: Path,
    workspace: str | None,
    task_id: int,
    from_agent: str,
    to_agent: str,
    message: str,
) -> None:
    """Called from HandoffQueue.enqueue. No-op unless (from,to) is a kickback."""
    if not workspace:
        return
    if (from_agent, to_agent) not in _KICKBACK_PAIRS:
        return
    try:
        # Build a short title from the first line of the message.
        first_line = message.strip().splitlines()[0] if message.strip() else "(no detail)"
        title_role = "Reviewer flagged" if from_agent == "reviewer" else \
                     "QA flagged" if from_agent == "qa" else \
                     "Backtest flagged"
        title = f"{title_role}: {first_line[:80]}"
        await MemoryStore(db_path).insert(
            workspace=workspace,
            type="lesson",
            agent_source=from_agent,
            title=title,
            body=message,
            related_task=task_id,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "memory.capture.on_handoff_kickback.failed",
            task_id=task_id, workspace=workspace,
        )
```

- [ ] **Step 4: Wire into `HandoffQueue.enqueue`**

The capture needs to know the current workspace. `HandoffQueue` doesn't currently — it's just a SQLite repo. To avoid passing workspace through every call site, capture in a centralised orchestrator dispatch path instead.

Find the orchestrator's handoff-dispatch loop:

Run: `grep -rn "claim()\|claim_next\|handoff_queue.claim" agent_hub/orchestrator/`

It lives in the orchestrator router/handoff loop. Hook there:

In `agent_hub/orchestrator/router.py` (or wherever the dispatch loop pops a handoff), immediately after `claim()` succeeds, add:

```python
            # Capture reverse-direction handoffs as lessons.
            from agent_hub.memory.capture import on_handoff_kickback
            workspace = str(self.runner.workspace) if self.runner.workspace else None
            await on_handoff_kickback(
                db_path=self.settings.database_path,
                workspace=workspace,
                task_id=row.task_id,
                from_agent=row.from_agent,
                to_agent=row.to_agent,
                message=row.message,
            )
```

If `router.py` doesn't have direct access to `self.runner` and `self.settings`, find the equivalent fields (or pass them in via the constructor) — match the existing pattern used for `push.py` and other orchestrator integrations.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_memory_capture.py tests/test_handoff_queue.py tests/test_handoff_loop.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/memory/capture.py \
    agent_hub/orchestrator/router.py \
    tests/test_memory_capture.py
git commit -m "feat(memory): capture lessons on reviewer/qa kickback"
```

---

## Task 10: Preference candidate detection + Telegram inline-keyboard handler

**Files:**
- Create: `agent_hub/memory/preferences.py`
- Modify: `agent_hub/telegram_bot/bot.py`
- Modify: `agent_hub/memory/capture.py`
- Test: `tests/test_memory_preferences.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_preferences.py`:

```python
"""Tests for preference-candidate detection."""

from __future__ import annotations

import pytest

from agent_hub.memory.preferences import looks_like_preference


@pytest.mark.parametrize("text,expected", [
    ("don't add code comments", True),
    ("Don't add code comments", True),
    ("dont add code comments", True),  # missing apostrophe
    ("always use prepared statements", True),
    ("never mock the database", True),
    ("stop summarizing what you did", True),
    ("from now on, prefer Drizzle", True),
    ("prefer one bundled PR", True),
    ("please don't squash commits", True),
    ("@pm build me a thing", False),
    ("what is the status?", False),
    ("/approve 42", False),
])
def test_looks_like_preference(text, expected):
    assert looks_like_preference(text) is expected
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_memory_preferences.py -v`
Expected: failures — module missing.

- [ ] **Step 3: Create `agent_hub/memory/preferences.py`**

```python
"""Cheap, deterministic preference detection on user Telegram messages.

We avoid LLM-based classification on purpose — it's a per-message overhead on
a hot path, and 'maybe a preference' is a soft signal. We let the user
confirm via inline-keyboard before anything lands in memory.
"""

from __future__ import annotations

import re


_PREFERENCE_MARKERS = re.compile(
    r"\b(don'?t|stop|never|always|from now on|prefer|please don'?t)\b",
    re.IGNORECASE,
)


def looks_like_preference(text: str) -> bool:
    """True if the text contains a corrective/imperative marker.

    False positives are fine — the user is asked to confirm before
    anything is written. False negatives mean a preference slips
    through unnoticed, which is recoverable via `/remember`.
    """
    if not text or text.startswith("/"):
        return False
    return _PREFERENCE_MARKERS.search(text) is not None
```

- [ ] **Step 4: Run preference tests**

Run: `pytest tests/test_memory_preferences.py -v`
Expected: all pass.

- [ ] **Step 5: Add `on_user_preference_save` capture hook**

In `agent_hub/memory/capture.py`:

```python
async def on_user_preference_save(
    *,
    db_path: Path,
    workspace: str | None,
    body: str,
) -> int | None:
    """Save a user-confirmed preference. Returns the new row id, or None."""
    if not workspace:
        return None
    try:
        return await MemoryStore(db_path).insert(
            workspace=workspace,
            type="preference",
            agent_source="user",
            title=body[:80],
            body=body,
        )
    except Exception:  # noqa: BLE001
        log.exception("memory.capture.on_user_preference_save.failed")
        return None
```

- [ ] **Step 6: Wire detection + inline keyboard into the bot**

In `agent_hub/telegram_bot/bot.py`, in the message handler that processes incoming user text (find it via `grep -n "message_handler\|on_text\|process_message" agent_hub/telegram_bot/bot.py`), add — **after** existing slash-command routing and **before** the message is sent to the PM:

```python
        from agent_hub.memory.preferences import looks_like_preference

        if looks_like_preference(text):
            workspace = str(self.runner.workspace) if self.runner.workspace else None
            if workspace:
                # Inline keyboard prompt; payload encodes workspace + text.
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("💾 Save preference", callback_data="memory_save"),
                    InlineKeyboardButton("Skip", callback_data="memory_skip"),
                ]])
                # Stash the candidate keyed on (chat_id, message_id) — bot has
                # short-term in-memory storage for inline-callback payloads.
                self._pending_preferences[update.message.message_id] = (workspace, text)
                await update.message.reply_text(
                    "💡 Save as project preference?", reply_markup=kb,
                )
```

Add an instance dict to `Bot.__init__`:

```python
        self._pending_preferences: dict[int, tuple[str, str]] = {}
```

Add a callback-query handler:

```python
    async def _on_memory_callback(self, update, context):
        from agent_hub.memory.capture import on_user_preference_save

        query = update.callback_query
        await query.answer()
        # Telegram tags the callback against the bot's message; the user's
        # original message is the *reply target* — we look up by that id.
        reply_to = query.message.reply_to_message
        key = reply_to.message_id if reply_to else None
        candidate = self._pending_preferences.pop(key, None) if key else None
        if candidate is None:
            await query.edit_message_text("(candidate expired)")
            return
        workspace, text = candidate
        if query.data == "memory_save":
            await on_user_preference_save(
                db_path=self.settings.database_path,
                workspace=workspace,
                body=text,
            )
            await query.edit_message_text("✅ Saved as preference.")
        else:
            await query.edit_message_text("Skipped.")
```

Register the handler in the bot's setup method (look for where other `CommandHandler` / `MessageHandler` are registered) — `CallbackQueryHandler(self._on_memory_callback, pattern=r"^memory_(save|skip)$")`.

- [ ] **Step 7: Manual smoke + commit**

Tests for the bot wiring itself are out of scope (PTB integration tests live in `tests/test_surface_telegram.py` patterns; add only if you have an established harness — otherwise verify manually by running the bot once with `/start` and a "don't add comments" message). Commit when the unit tests for `preferences.py` and `capture.py` pass:

```bash
git add agent_hub/memory/preferences.py \
    agent_hub/memory/capture.py \
    agent_hub/telegram_bot/bot.py \
    tests/test_memory_preferences.py
git commit -m "feat(memory): preference-candidate detection + inline-keyboard save"
```

---

## Task 11: Telegram `/memory`, `/forget`, `/remember`, `/memory clear`

**Files:**
- Create: `agent_hub/telegram_bot/commands/memory_cmd.py`
- Modify: `agent_hub/telegram_bot/bot.py`
- Test: `tests/test_commands_memory.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_commands_memory.py`:

```python
"""Tests for the /memory, /forget, /remember commands.

Pure handlers — they take all dependencies as args, return strings.
"""

from __future__ import annotations

import pytest

from agent_hub.db import Database
from agent_hub.memory.store import MemoryStore
from agent_hub.telegram_bot.commands.memory_cmd import (
    handle_forget,
    handle_memory_clear,
    handle_memory_list,
    handle_remember,
)


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return temp_db_path


@pytest.mark.asyncio
async def test_handle_memory_list_empty(db_path):
    out = await handle_memory_list(
        db_path=db_path, workspace=r"C:\dev\foo", type_filter=None,
    )
    assert "no project memory" in out.lower()


@pytest.mark.asyncio
async def test_handle_memory_list_groups_by_type(db_path):
    ws = r"C:\dev\foo"
    store = MemoryStore(db_path)
    await store.insert(workspace=ws, type="project_fact", agent_source="x",
                       title="FACT-X", body="b")
    await store.insert(workspace=ws, type="lesson", agent_source="x",
                       title="LESSON-X", body="b")
    out = await handle_memory_list(
        db_path=db_path, workspace=ws, type_filter=None,
    )
    assert "FACT-X" in out
    assert "LESSON-X" in out


@pytest.mark.asyncio
async def test_handle_memory_list_with_type_filter(db_path):
    ws = r"C:\dev\foo"
    store = MemoryStore(db_path)
    await store.insert(workspace=ws, type="project_fact", agent_source="x",
                       title="FACT-X", body="b")
    await store.insert(workspace=ws, type="lesson", agent_source="x",
                       title="LESSON-X", body="b")
    out = await handle_memory_list(
        db_path=db_path, workspace=ws, type_filter="lessons",
    )
    assert "LESSON-X" in out
    assert "FACT-X" not in out


@pytest.mark.asyncio
async def test_handle_forget_archives(db_path):
    ws = r"C:\dev\foo"
    new_id = await MemoryStore(db_path).insert(
        workspace=ws, type="lesson", agent_source="x", title="X", body="b",
    )
    out = await handle_forget(db_path=db_path, entry_id=new_id, workspace=ws)
    assert "forgot" in out.lower()
    rows = await MemoryStore(db_path).list(workspace=ws, type="lesson")
    assert rows == []


@pytest.mark.asyncio
async def test_handle_forget_unknown_id(db_path):
    out = await handle_forget(
        db_path=db_path, entry_id=999, workspace=r"C:\dev\foo",
    )
    assert "not found" in out.lower()


@pytest.mark.asyncio
async def test_handle_forget_other_workspace_refused(db_path):
    """Can't forget an entry from a different workspace by id alone."""
    new_id = await MemoryStore(db_path).insert(
        workspace=r"C:\dev\foo", type="lesson", agent_source="x",
        title="X", body="b",
    )
    out = await handle_forget(
        db_path=db_path, entry_id=new_id, workspace=r"C:\dev\bar",
    )
    assert "not found" in out.lower()
    # And confirm row is still there
    rows = await MemoryStore(db_path).list(workspace=r"C:\dev\foo", type="lesson")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_handle_remember_creates_preference(db_path):
    ws = r"C:\dev\foo"
    out = await handle_remember(
        db_path=db_path, workspace=ws, text="prefer terse output",
    )
    assert "saved" in out.lower()
    rows = await MemoryStore(db_path).list(workspace=ws, type="preference")
    assert len(rows) == 1
    assert rows[0]["body"] == "prefer terse output"


@pytest.mark.asyncio
async def test_handle_memory_clear_requires_confirm(db_path):
    ws = r"C:\dev\foo"
    await MemoryStore(db_path).insert(
        workspace=ws, type="lesson", agent_source="x", title="X", body="b",
    )
    out = await handle_memory_clear(db_path=db_path, workspace=ws, confirm=False)
    assert "confirm" in out.lower()
    rows = await MemoryStore(db_path).list(workspace=ws)
    assert len(rows) == 1  # still there


@pytest.mark.asyncio
async def test_handle_memory_clear_with_confirm(db_path):
    ws = r"C:\dev\foo"
    await MemoryStore(db_path).insert(
        workspace=ws, type="lesson", agent_source="x", title="X", body="b",
    )
    out = await handle_memory_clear(db_path=db_path, workspace=ws, confirm=True)
    assert "cleared" in out.lower()
    rows = await MemoryStore(db_path).list(workspace=ws)
    assert rows == []


@pytest.mark.asyncio
async def test_handle_memory_clear_scoped_to_workspace(db_path):
    """Clearing one workspace must not touch another."""
    store = MemoryStore(db_path)
    await store.insert(workspace=r"C:\dev\foo", type="lesson",
                       agent_source="x", title="X", body="b")
    await store.insert(workspace=r"C:\dev\bar", type="lesson",
                       agent_source="x", title="X", body="b")
    await handle_memory_clear(db_path=db_path, workspace=r"C:\dev\foo", confirm=True)
    assert await store.list(workspace=r"C:\dev\foo") == []
    assert len(await store.list(workspace=r"C:\dev\bar")) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_commands_memory.py -v`
Expected: failures — module missing.

- [ ] **Step 3: Create `agent_hub/telegram_bot/commands/memory_cmd.py`**

```python
"""Pure handlers for /memory, /forget, /remember, /memory clear."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from agent_hub.memory.store import MemoryStore

# UI labels for type filters.
_TYPE_ALIASES = {
    "facts":       "project_fact",
    "lessons":     "lesson",
    "preferences": "preference",
    "decisions":   "decision",
}


_TYPE_HEADINGS = {
    "project_fact": "🧱 Conventions",
    "preference":   "❤️ Preferences",
    "lesson":       "📚 Lessons",
    "decision":     "🧭 Decisions",
}


async def handle_memory_list(
    *,
    db_path: Path,
    workspace: str | None,
    type_filter: str | None,
) -> str:
    if not workspace:
        return "No active workspace. Set one with /workspace <path>."
    type_arg = _TYPE_ALIASES.get(type_filter) if type_filter else None
    store = MemoryStore(db_path)
    rows = await store.list(workspace=workspace, type=type_arg)
    if not rows:
        return f"No project memory for `{workspace}`."

    # Group by type.
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)

    lines = [f"📒 Memory for `{workspace}`", ""]
    for t in ("project_fact", "preference", "lesson", "decision"):
        bucket = by_type.get(t, [])
        if not bucket:
            continue
        lines.append(f"**{_TYPE_HEADINGS[t]}**")
        for r in bucket[:20]:  # cap per type in listing
            lines.append(f"  #{r['id']}  {r['title']}  (used {r['use_count']}×)")
        lines.append("")
    return "\n".join(lines).rstrip()


async def handle_forget(
    *,
    db_path: Path,
    entry_id: int,
    workspace: str | None,
) -> str:
    """Archive a memory entry by id, scoped to the active workspace.

    Refuses to touch entries in other workspaces — id alone isn't enough
    authorization to mutate memory the user can't currently see.
    """
    if not workspace:
        return "No active workspace. Set one with /workspace <path>."
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            "SELECT title, workspace FROM project_memory "
            "WHERE id = ? AND archived = 0",
            (entry_id,),
        )
        row = await cur.fetchone()
    if row is None or row["workspace"] != workspace:
        return f"Memory entry #{entry_id} not found in `{workspace}`."
    await MemoryStore(db_path).archive(entry_id)
    return f"🗑 Forgot #{entry_id}: {row['title']}"


async def handle_remember(
    *,
    db_path: Path,
    workspace: str | None,
    text: str,
) -> str:
    if not workspace:
        return "No active workspace. Set one with /workspace <path>."
    text = (text or "").strip()
    if not text:
        return "Usage: /remember <preference text>"
    await MemoryStore(db_path).insert(
        workspace=workspace,
        type="preference",
        agent_source="user",
        title=text[:80],
        body=text,
    )
    return f"💾 Saved as preference for `{workspace}`."


async def handle_memory_clear(
    *,
    db_path: Path,
    workspace: str | None,
    confirm: bool,
) -> str:
    if not workspace:
        return "No active workspace. Set one with /workspace <path>."
    if not confirm:
        return (
            f"⚠️  This will archive ALL memory for `{workspace}`. "
            f"Re-run as `/memory clear confirm` to proceed."
        )
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE project_memory SET archived = 1 WHERE workspace = ?",
            (workspace,),
        )
        await conn.commit()
    return f"🗑 Cleared all memory for `{workspace}`."
```

- [ ] **Step 4: Wire commands into the bot**

In `agent_hub/telegram_bot/bot.py`, register the three commands. Find an existing `CommandHandler` registration site (e.g., next to `tasks_cmd`) and add:

```python
        from telegram.ext import CommandHandler
        application.add_handler(CommandHandler("memory", self._cmd_memory))
        application.add_handler(CommandHandler("forget", self._cmd_forget))
        application.add_handler(CommandHandler("remember", self._cmd_remember))
```

Add the methods:

```python
    async def _cmd_memory(self, update, context):
        from agent_hub.telegram_bot.commands.memory_cmd import (
            handle_memory_list, handle_memory_clear,
        )
        args = context.args or []
        workspace = str(self.runner.workspace) if self.runner.workspace else None
        # `/memory clear [confirm]`
        if args and args[0].lower() == "clear":
            confirm = len(args) > 1 and args[1].lower() == "confirm"
            text = await handle_memory_clear(
                db_path=self.settings.database_path,
                workspace=workspace, confirm=confirm,
            )
        else:
            type_filter = args[0].lower() if args else None
            text = await handle_memory_list(
                db_path=self.settings.database_path,
                workspace=workspace, type_filter=type_filter,
            )
        await update.message.reply_text(text)

    async def _cmd_forget(self, update, context):
        from agent_hub.telegram_bot.commands.memory_cmd import handle_forget
        args = context.args or []
        if not args or not args[0].isdigit():
            await update.message.reply_text("Usage: /forget <id>")
            return
        workspace = str(self.runner.workspace) if self.runner.workspace else None
        text = await handle_forget(
            db_path=self.settings.database_path,
            entry_id=int(args[0]), workspace=workspace,
        )
        await update.message.reply_text(text)

    async def _cmd_remember(self, update, context):
        from agent_hub.telegram_bot.commands.memory_cmd import handle_remember
        text_in = " ".join(context.args or [])
        workspace = str(self.runner.workspace) if self.runner.workspace else None
        text_out = await handle_remember(
            db_path=self.settings.database_path,
            workspace=workspace, text=text_in,
        )
        await update.message.reply_text(text_out)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_commands_memory.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/telegram_bot/commands/memory_cmd.py \
    agent_hub/telegram_bot/bot.py \
    tests/test_commands_memory.py
git commit -m "feat(memory): /memory, /forget, /remember, /memory clear Telegram commands"
```

---

## Task 12: MCP tool `memory.note` (for project_fact)

**Files:**
- Create: `agent_hub/mcp_server/tools/memory_tools.py`
- Modify: `agent_hub/mcp_server/__init__.py` (or equivalent registration site)
- Test: `tests/test_mcp_tools_memory.py`

- [ ] **Step 1: Inspect the existing MCP tool pattern**

Read one of the existing tools to match the pattern:

Run: `cat agent_hub/mcp_server/tools/tasks_tools.py` — note how MCP tools are declared (decorator, signature, error envelope).

- [ ] **Step 2: Write the failing test**

Create `tests/test_mcp_tools_memory.py`:

```python
"""Test the memory.note MCP tool — the project_fact escape hatch.

Mirrors tests/test_mcp_tools_tasks.py pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_hub.db import Database
from agent_hub.memory.store import MemoryStore


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return temp_db_path


@pytest.mark.asyncio
async def test_memory_note_inserts_project_fact(db_path, monkeypatch, tmp_path):
    from agent_hub.mcp_server.tools import memory_tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AGENT_HUB_DB", str(db_path))
    monkeypatch.setenv("AGENT_HUB_WORKSPACE", str(workspace))

    # The tool is exposed via FastMCP — call its underlying function directly.
    result = await memory_tools.memory_note(
        type="project_fact",
        title="Build cmd is npm run build:prod",
        body="The package.json has it under build:prod, not build.",
    )
    assert result.get("ok") is True

    rows = await MemoryStore(db_path).list(
        workspace=str(workspace), type="project_fact",
    )
    assert len(rows) == 1
    assert rows[0]["title"] == "Build cmd is npm run build:prod"
    # agent_source comes from MCP context — default if not provided.
    assert rows[0]["agent_source"] is not None


@pytest.mark.asyncio
async def test_memory_note_rejects_non_project_fact(db_path, monkeypatch, tmp_path):
    from agent_hub.mcp_server.tools import memory_tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AGENT_HUB_DB", str(db_path))
    monkeypatch.setenv("AGENT_HUB_WORKSPACE", str(workspace))

    result = await memory_tools.memory_note(
        type="lesson",
        title="X", body="b",
    )
    assert result.get("ok") is False
    assert "project_fact" in result.get("error", "").lower()
```

- [ ] **Step 3: Run tests to verify failure**

Run: `pytest tests/test_mcp_tools_memory.py -v`
Expected: failures — module missing.

- [ ] **Step 4: Create `agent_hub/mcp_server/tools/memory_tools.py`**

Match the local FastMCP pattern (from inspecting `tasks_tools.py`). Skeleton — adapt naming/registration to whatever pattern the repo uses:

```python
"""MCP tool: memory.note — record a project_fact."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

from agent_hub.memory.store import MemoryStore

log = structlog.get_logger(__name__)


# Registration uses the same FastMCP/mcp pattern as tasks_tools.py.
# Replace `register(mcp)` below with whatever the project's MCP scaffold expects.

async def memory_note(
    *,
    type: str,
    title: str,
    body: str,
    agent_source: str | None = None,
) -> dict[str, Any]:
    """Record a project-level fact.

    `type` MUST be `project_fact` in MVP. Lesson/preference/decision are
    written only via auto-capture (see agent_hub.memory.capture).

    Reads the active workspace from $AGENT_HUB_WORKSPACE (set by the
    MCP server launcher; matches the pattern used by other tools).
    """
    if type != "project_fact":
        return {
            "ok": False,
            "error": "memory.note can only record type=project_fact; "
                     "other types are auto-captured.",
        }

    db_env = os.environ.get("AGENT_HUB_DB")
    ws_env = os.environ.get("AGENT_HUB_WORKSPACE")
    if not db_env or not ws_env:
        return {"ok": False, "error": "AGENT_HUB_DB or AGENT_HUB_WORKSPACE unset"}

    try:
        new_id = await MemoryStore(Path(db_env)).insert(
            workspace=ws_env,
            type="project_fact",
            agent_source=agent_source or "agent",
            title=title.strip()[:80],
            body=body.strip(),
        )
        return {"ok": True, "id": new_id}
    except Exception as exc:  # noqa: BLE001
        log.exception("memory.note.failed")
        return {"ok": False, "error": str(exc)}


def register(mcp) -> None:
    """Register memory.* tools with the FastMCP server."""
    @mcp.tool()
    async def memory__note(type: str, title: str, body: str) -> dict[str, Any]:
        """Record a project-level fact (build commands, stack, conventions)."""
        return await memory_note(type=type, title=title, body=body)
```

In the MCP server's tool-registration site (`agent_hub/mcp_server/__init__.py` or `agent_hub/mcp_server/__main__.py` — wherever `tasks_tools.register(mcp)` is called), add `memory_tools.register(mcp)`.

Note: setting `AGENT_HUB_WORKSPACE` in the MCP server env requires a small change to `runner_options.build_mcp_server_config` — add `"AGENT_HUB_WORKSPACE": str(cwd)` to the env dict (only when cwd is not None). This already mirrors how `AGENT_HUB_DB` is set.

- [ ] **Step 5: Update `build_mcp_server_config` to pass workspace**

In `agent_hub/agents/runner_options.py`, update the function signature and env dict:

```python
def build_mcp_server_config(db_path: Path, cwd: Path | None = None) -> dict[str, Any]:
    # ... existing setup ...
    env = {
        **os.environ,
        "AGENT_HUB_DB": str(db_path),
        "PYTHONPATH": python_path,
    }
    if cwd is not None:
        env["AGENT_HUB_WORKSPACE"] = str(cwd)
    return {
        "agent_hub": {
            "command": sys.executable,
            "args": ["-m", "agent_hub.mcp_server"],
            "env": env,
        },
    }
```

And in `build_sdk_options`, update the call:

```python
        "mcp_servers": build_mcp_server_config(db_path, cwd=cwd),
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_mcp_tools_memory.py tests/test_mcp_server_e2e.py tests/test_runner_options.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add agent_hub/mcp_server/ agent_hub/agents/runner_options.py \
    tests/test_mcp_tools_memory.py
git commit -m "feat(memory): MCP memory.note tool (project_fact only) + workspace env wiring"
```

---

## Task 13: Smoke test — assert a decision is captured end-to-end

**Files:**
- Modify: `tests/test_smoke.py`

- [ ] **Step 1: Locate the existing smoke test**

Run: `cat tests/test_smoke.py | head -80`

Read enough of the smoke test to find where `/approve` is exercised and where the final assertions happen.

- [ ] **Step 2: Add the assertion**

After the smoke test's `/approve` step completes and before it tears down, add:

```python
    # Memory capture: /approve should have recorded a `decision` entry
    # for the architect's design.
    from agent_hub.memory.store import MemoryStore
    decisions = await MemoryStore(db_path).list(
        workspace=str(workspace), type="decision",
    )
    assert len(decisions) >= 1, "expected at least one decision captured by /approve"
    # And the related_task should match the smoke-test task id.
    assert any(d["related_task"] == task_id for d in decisions)
```

Adjust the variable names (`db_path`, `workspace`, `task_id`) to match what the smoke test already uses — read the existing code first.

- [ ] **Step 3: Run the smoke test (real API; cost ~$0.05–0.10)**

```powershell
$env:RUN_SMOKE_TESTS = "1"
.venv\Scripts\python.exe -m pytest tests/test_smoke.py -v -s
```

Expected: PASS, including the new decision assertion.

If the smoke test passes everything except the new assertion: read the architect's design output — if the architect's comment didn't actually post in this run (rare; check task_events), the capture has nothing to grab. Tighten the assertion to depend only on the architect having commented (skip the assertion gracefully if the comment is empty). This is the only place in the plan where the assertion can legitimately not hold, due to LLM variability.

- [ ] **Step 4: Commit**

```bash
git add tests/test_smoke.py
git commit -m "test(memory): smoke test asserts decision captured on /approve"
```

---

## Final verification

After all tasks:

```bash
cd C:\dev\agent-hub
.venv\Scripts\python.exe -m pytest -q
```

Expected: full test suite passes (existing 267 + the new memory tests).

Run the smoke test once if not already:

```powershell
$env:RUN_SMOKE_TESTS = "1"
.venv\Scripts\python.exe -m pytest tests/test_smoke.py -v -s
```

Expected: PASS.

Manual sanity in Telegram (optional but recommended):

1. `/workspace C:\dev\some-project`
2. Send: "don't add code comments" → bot offers Save/Skip → tap Save.
3. `/memory` → confirms the preference is listed.
4. File a task; on approve, `/memory` should also list the decision.
5. `/forget <id>` → row disappears from `/memory`.
