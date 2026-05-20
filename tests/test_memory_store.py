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
