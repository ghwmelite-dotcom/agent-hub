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
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO task_events (task_id, ts, actor, kind, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (999, "2026-05-17T00:00:00Z", "test", "comment", "{}"),
            )
            await conn.commit()
