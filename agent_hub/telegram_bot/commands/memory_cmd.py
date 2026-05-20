"""Pure handlers for /memory, /forget, /remember, /memory clear."""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from agent_hub.memory.store import MemoryStore


_TYPE_ALIASES = {
    "facts":       "project_fact",
    "lessons":     "lesson",
    "preferences": "preference",
    "decisions":   "decision",
}


_TYPE_HEADINGS = {
    "project_fact": "Conventions",
    "preference":   "Preferences",
    "lesson":       "Lessons",
    "decision":     "Decisions",
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

    by_type: dict[str, list[dict]] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)

    lines = [f"Memory for `{workspace}`", ""]
    for t in ("project_fact", "preference", "lesson", "decision"):
        bucket = by_type.get(t, [])
        if not bucket:
            continue
        lines.append(f"**{_TYPE_HEADINGS[t]}**")
        for r in bucket[:20]:
            lines.append(f"  #{r['id']}  {r['title']}  (used {r['use_count']}x)")
        lines.append("")
    return "\n".join(lines).rstrip()


async def handle_forget(
    *,
    db_path: Path,
    entry_id: int,
    workspace: str | None,
) -> str:
    """Archive a memory entry by id, scoped to the active workspace."""
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
    return f"Forgot #{entry_id}: {row['title']}"


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
    return f"Saved as preference for `{workspace}`."


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
            f"This will archive ALL memory for `{workspace}`. "
            f"Re-run as `/memory clear confirm` to proceed."
        )
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE project_memory SET archived = 1 WHERE workspace = ?",
            (workspace,),
        )
        await conn.commit()
    return f"Cleared all memory for `{workspace}`."
