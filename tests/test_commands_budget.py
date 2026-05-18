"""Tests for /budget — view, set, disable the cumulative spend cap."""

from __future__ import annotations

import pytest

from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.budget_cmd import (
    clear_budget_cap,
    get_budget_cap,
    handle_budget,
    set_budget_cap,
)


@pytest.fixture
async def db_path(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return temp_db_path


@pytest.mark.asyncio
async def test_no_args_shows_no_cap_and_zero_spend(db_path):
    reply = await handle_budget(args=[], db_path=db_path)
    assert "Cap: (none" in reply
    assert "$0.0000" in reply


@pytest.mark.asyncio
async def test_set_cap_persists(db_path):
    reply = await handle_budget(args=["5.00"], db_path=db_path)
    assert "Cap set to $5.00" in reply or "set to $5" in reply.lower()
    assert await get_budget_cap(db_path) == 5.00


@pytest.mark.asyncio
async def test_set_cap_handles_dollar_sign(db_path):
    await handle_budget(args=["$2.50"], db_path=db_path)
    assert await get_budget_cap(db_path) == 2.50


@pytest.mark.asyncio
async def test_view_shows_spend_and_headroom(db_path):
    repo = TaskRepository(db_path)
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.add_cost(t.id, 1.25)
    await set_budget_cap(db_path, 5.00)

    reply = await handle_budget(args=[], db_path=db_path)
    assert "Cap: $5.00" in reply
    assert "1.25" in reply
    assert "3.75" in reply  # remaining = 5.00 - 1.25


@pytest.mark.asyncio
async def test_over_cap_warning(db_path):
    repo = TaskRepository(db_path)
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.add_cost(t.id, 10.0)
    await set_budget_cap(db_path, 5.00)

    reply = await handle_budget(args=[], db_path=db_path)
    assert "⚠" in reply
    assert "over by" in reply.lower()


@pytest.mark.asyncio
async def test_off_clears_cap(db_path):
    await set_budget_cap(db_path, 5.00)
    reply = await handle_budget(args=["off"], db_path=db_path)
    assert "removed" in reply.lower()
    assert await get_budget_cap(db_path) is None


@pytest.mark.asyncio
async def test_negative_cap_rejected(db_path):
    reply = await handle_budget(args=["-1"], db_path=db_path)
    assert "positive" in reply.lower()
    assert await get_budget_cap(db_path) is None


@pytest.mark.asyncio
async def test_garbage_arg_shows_usage(db_path):
    reply = await handle_budget(args=["banana"], db_path=db_path)
    assert "Usage:" in reply
    assert await get_budget_cap(db_path) is None


@pytest.mark.asyncio
async def test_clear_budget_cap_helper(db_path):
    await set_budget_cap(db_path, 5.00)
    await clear_budget_cap(db_path)
    assert await get_budget_cap(db_path) is None
