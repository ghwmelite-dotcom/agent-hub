"""Tests for AgentSessionStore — persistent (agent, task) → session_id map."""

from __future__ import annotations

import re

import pytest

from agent_hub.agents.session_store import AgentSessionStore
from agent_hub.db import Database


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@pytest.fixture
async def store(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return AgentSessionStore(temp_db_path)


@pytest.mark.asyncio
async def test_get_or_create_returns_new_uuid(store):
    sid = await store.get_or_create(agent_name="pm", task_id=1)
    assert _UUID_RE.match(sid)


@pytest.mark.asyncio
async def test_get_or_create_reuses_existing(store):
    """Calling again for the same (agent, task) returns the same UUID —
    this is the property that makes restart-resume work."""
    first = await store.get_or_create(agent_name="pm", task_id=1)
    second = await store.get_or_create(agent_name="pm", task_id=1)
    assert first == second


@pytest.mark.asyncio
async def test_different_task_ids_get_different_sessions(store):
    s1 = await store.get_or_create(agent_name="pm", task_id=1)
    s2 = await store.get_or_create(agent_name="pm", task_id=2)
    assert s1 != s2


@pytest.mark.asyncio
async def test_different_agents_get_different_sessions(store):
    s1 = await store.get_or_create(agent_name="pm", task_id=1)
    s2 = await store.get_or_create(agent_name="architect", task_id=1)
    assert s1 != s2


@pytest.mark.asyncio
async def test_no_task_id_uses_sentinel(store):
    """task_id=None must produce a stable row distinct from task_id=N."""
    s_none = await store.get_or_create(agent_name="pm", task_id=None)
    s_one = await store.get_or_create(agent_name="pm", task_id=1)
    s_none_again = await store.get_or_create(agent_name="pm", task_id=None)
    assert s_none != s_one
    assert s_none == s_none_again


@pytest.mark.asyncio
async def test_get_returns_none_when_absent(store):
    assert await store.get(agent_name="pm", task_id=1) is None
    await store.get_or_create(agent_name="pm", task_id=1)
    assert await store.get(agent_name="pm", task_id=1) is not None


@pytest.mark.asyncio
async def test_forget_drops_row(store):
    sid = await store.get_or_create(agent_name="pm", task_id=1)
    await store.forget(agent_name="pm", task_id=1)
    assert await store.get(agent_name="pm", task_id=1) is None
    # Next get_or_create generates a fresh UUID
    new_sid = await store.get_or_create(agent_name="pm", task_id=1)
    assert new_sid != sid


@pytest.mark.asyncio
async def test_forget_is_idempotent(store):
    await store.forget(agent_name="pm", task_id=999)  # no row exists; must not raise


@pytest.mark.asyncio
async def test_session_persists_across_store_instances(temp_db_path):
    """Models a process restart: build a new store against the same DB,
    same (agent, task_id) — must return the same UUID."""
    db = Database(temp_db_path)
    await db.init()
    s1 = await AgentSessionStore(temp_db_path).get_or_create(
        agent_name="pm", task_id=1,
    )
    s2 = await AgentSessionStore(temp_db_path).get_or_create(
        agent_name="pm", task_id=1,
    )
    assert s1 == s2


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
