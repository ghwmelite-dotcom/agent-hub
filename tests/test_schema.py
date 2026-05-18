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
        "created_at", "updated_at", "cost_usd_total",
    }


@pytest.mark.asyncio
async def test_all_new_tables_exist(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    expected = {
        "tasks", "task_events", "handoff_queue", "gates", "worktrees",
        "agent_sessions",
    }
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
        "notified_at", "last_reminder_at",
    }


@pytest.mark.asyncio
async def test_tasks_cost_total_migration_idempotent(temp_db_path):
    """A pre-existing DB without `tasks.cost_usd_total` gets it added;
    second init() is a no-op."""
    async with aiosqlite.connect(temp_db_path) as conn:
        await conn.execute(
            "CREATE TABLE tasks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "parent_id INTEGER,"
            "title TEXT NOT NULL,"
            "description TEXT NOT NULL,"
            "status TEXT NOT NULL,"
            "owner TEXT,"
            "worktree_path TEXT,"
            "branch_name TEXT,"
            "origin_chat_id INTEGER NOT NULL,"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL"
            ")"
        )
        await conn.commit()

    await Database(temp_db_path).init()
    async with aiosqlite.connect(temp_db_path) as conn:
        rows = await (await conn.execute("PRAGMA table_info(tasks)")).fetchall()
    cols = {r[1] for r in rows}
    assert "cost_usd_total" in cols

    await Database(temp_db_path).init()


@pytest.mark.asyncio
async def test_gates_notified_at_migration_idempotent(temp_db_path):
    """A pre-existing DB without `notified_at` gets it added; init() can
    be called again with no error and no duplicate-column issue."""
    # Seed a DB that looks like the pre-migration schema
    async with aiosqlite.connect(temp_db_path) as conn:
        await conn.execute(
            "CREATE TABLE gates ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "task_id INTEGER NOT NULL,"
            "kind TEXT NOT NULL,"
            "artifact_path TEXT,"
            "summary TEXT,"
            "requested_at TEXT NOT NULL,"
            "resolved_at TEXT,"
            "resolution TEXT"
            ")"
        )
        await conn.commit()

    # First init: should ALTER TABLE in the new column
    await Database(temp_db_path).init()
    async with aiosqlite.connect(temp_db_path) as conn:
        rows = await (await conn.execute("PRAGMA table_info(gates)")).fetchall()
    cols = {r[1] for r in rows}
    assert "notified_at" in cols

    # Second init: must be a no-op (no duplicate-column error)
    await Database(temp_db_path).init()


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
