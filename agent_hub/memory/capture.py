"""Auto-capture hooks. Called by the orchestrator at key events.

Memory writes never raise — failures are logged and swallowed. The orchestrator
is the source of truth for the actual task flow; missing memory is a degraded
experience but not a broken pipeline.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from agent_hub.memory.store import MemoryStore

log = structlog.get_logger(__name__)


async def on_design_approved(
    *,
    db_path: Path,
    workspace: str | None,
    task_id: int,
    task_title: str,
    design_text: str,
    agent_name: str,
) -> None:
    """Called from approve_cmd after the gate is resolved."""
    if not workspace:
        return
    try:
        await MemoryStore(db_path).insert(
            workspace=workspace,
            type="decision",
            agent_source=agent_name,
            title=f"Task #{task_id}: {task_title}",
            body=design_text,
            related_task=task_id,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "memory.capture.on_design_approved.failed",
            task_id=task_id, workspace=workspace,
        )


async def on_reject(
    *,
    db_path: Path,
    workspace: str | None,
    task_id: int,
    task_title: str,
    reason: str,
) -> None:
    """Called from reject_cmd after the gate is rejected."""
    if not workspace:
        return
    try:
        await MemoryStore(db_path).insert(
            workspace=workspace,
            type="lesson",
            agent_source="user",
            title=f"Rejected task #{task_id}: {task_title}",
            body=reason,
            related_task=task_id,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "memory.capture.on_reject.failed",
            task_id=task_id, workspace=workspace,
        )


# Direction pairs that count as a "kickback" (reverse-flow handoff).
_KICKBACK_PAIRS = {
    ("reviewer", "fullstack-engineer"),
    ("reviewer", "implementer"),
    ("qa", "fullstack-engineer"),
    ("qa", "implementer"),
    ("backtest-analyst", "fullstack-engineer"),
}


async def on_user_preference_save(
    *,
    db_path: Path,
    workspace: str | None,
    body: str,
) -> int | None:
    """Save a user-confirmed preference. Returns the new row id, or None."""
    if not workspace:
        return None
    try:
        return await MemoryStore(db_path).insert(
            workspace=workspace,
            type="preference",
            agent_source="user",
            title=body[:80],
            body=body,
        )
    except Exception:  # noqa: BLE001
        log.exception("memory.capture.on_user_preference_save.failed")
        return None


async def on_handoff_kickback(
    *,
    db_path: Path,
    workspace: str | None,
    task_id: int,
    from_agent: str,
    to_agent: str,
    message: str,
) -> None:
    """Called from the orchestrator handoff dispatch loop.

    No-op unless (from,to) is a known reverse-flow pair (reviewer → fullstack,
    qa → fullstack, etc.). Forward handoffs (fullstack → reviewer, reviewer → qa)
    are normal progress and don't produce lessons.
    """
    if not workspace:
        return
    if (from_agent, to_agent) not in _KICKBACK_PAIRS:
        return
    try:
        first_line = message.strip().splitlines()[0] if message.strip() else "(no detail)"
        title_role = (
            "Reviewer flagged" if from_agent == "reviewer"
            else "QA flagged" if from_agent == "qa"
            else "Backtest flagged"
        )
        title = f"{title_role}: {first_line[:80]}"
        await MemoryStore(db_path).insert(
            workspace=workspace,
            type="lesson",
            agent_source=from_agent,
            title=title,
            body=message,
            related_task=task_id,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "memory.capture.on_handoff_kickback.failed",
            task_id=task_id, workspace=workspace,
        )
