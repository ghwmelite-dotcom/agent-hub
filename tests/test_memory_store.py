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
