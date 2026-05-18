"""Restart-resume scan: on agent_hub boot, find tasks that were in
flight with no recent activity and DM the user.

We NEVER auto-resume — only surface the list. The user issues /resume
<id> to actually pick one back up.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from agent_hub.orchestrator.surface import MessageSurface


_IN_FLIGHT_STATUSES = ("planning", "design_review", "ready", "in_progress", "review")


async def scan_stale_tasks(
    *,
    db_path: Path,
    surface: MessageSurface,
    stale_after_minutes: int = 5,
    released_claims_count: int = 0,
) -> int:
    """DM each stale task's chat. Returns number of DMs sent.

    `released_claims_count`: how many handoff rows the orchestrator
    released on boot (claimed by the previous dead process). When >0
    the DM mentions it so the user knows something was recovered
    automatically rather than left in limbo.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - stale_after_minutes * 60

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        placeholders = ",".join("?" * len(_IN_FLIGHT_STATUSES))
        cur = await conn.execute(
            f"""
            SELECT t.id, t.title, t.status, t.origin_chat_id,
                   (SELECT ts FROM task_events
                    WHERE task_id = t.id
                    ORDER BY id DESC LIMIT 1) AS last_ts
            FROM tasks t
            WHERE t.status IN ({placeholders})
            """,
            _IN_FLIGHT_STATUSES,
        )
        rows = await cur.fetchall()

    sent = 0
    by_chat: dict[int, list[str]] = {}
    for row in rows:
        last_ts = row["last_ts"]
        if last_ts is None:
            continue
        last_dt = datetime.fromisoformat(last_ts)
        if last_dt.timestamp() > cutoff:
            continue
        chat_id = row["origin_chat_id"]
        line = f"  #{row['id']} {row['title']} ({row['status']})"
        by_chat.setdefault(chat_id, []).append(line)

    recovery_line = ""
    if released_claims_count > 0:
        plural = "s" if released_claims_count != 1 else ""
        recovery_line = (
            f"♻️ Released {released_claims_count} stuck handoff{plural} "
            f"from last shutdown — they'll be re-dispatched automatically.\n\n"
        )

    for chat_id, lines in by_chat.items():
        body = (
            recovery_line
            + "🔄 Tasks that were in flight at last shutdown:\n"
            + "\n".join(lines)
            + "\n\nReply /resume <id> to pick one back up."
        )
        await surface.dm(chat_id, body)
        sent += 1
    return sent
