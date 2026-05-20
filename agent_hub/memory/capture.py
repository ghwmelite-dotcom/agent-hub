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
