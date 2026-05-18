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
