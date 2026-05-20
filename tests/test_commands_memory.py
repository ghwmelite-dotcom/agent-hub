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
    assert len(rows) == 1


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
    store = MemoryStore(db_path)
    await store.insert(workspace=r"C:\dev\foo", type="lesson",
                       agent_source="x", title="X", body="b")
    await store.insert(workspace=r"C:\dev\bar", type="lesson",
                       agent_source="x", title="X", body="b")
    await handle_memory_clear(db_path=db_path, workspace=r"C:\dev\foo", confirm=True)
    assert await store.list(workspace=r"C:\dev\foo") == []
    assert len(await store.list(workspace=r"C:\dev\bar")) == 1
