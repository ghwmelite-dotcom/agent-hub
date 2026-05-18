"""Pure handler for /status.

One-shot health snapshot: workspace, handoff queue depth, unresolved
gates, active agent sessions, task counts by status.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


# Order matters — most "active" states first so the user reads them top-down.
_REPORT_ORDER = [
    TaskStatus.IN_PROGRESS,
    TaskStatus.REVIEW,
    TaskStatus.READY,
    TaskStatus.DESIGN_REVIEW,
    TaskStatus.PLANNING,
    TaskStatus.PENDING,
    TaskStatus.BLOCKED,
]


async def handle_status(
    *,
    db_path: Path,
    runner: Any | None = None,
) -> str:
    """Return a Telegram-friendly status line block."""
    repo = TaskRepository(db_path)
    gates = GateRepository(db_path)
    queue = HandoffQueue(db_path)

    pending_handoffs = await queue.count_pending()
    unresolved_gates = await gates.count_unresolved()
    total_cost = await repo.total_cost_usd()

    counts: dict[TaskStatus, int] = {}
    for status in _REPORT_ORDER:
        rows = await repo.list(status=status)
        counts[status] = len(rows)

    lines = ["📊 Agent Hub status"]
    if runner is not None:
        workspace = runner.workspace
        sessions = runner.active_session_count
        lines.append(
            f"Workspace: {workspace if workspace else '(none)'}"
        )
        lines.append(f"Active agent sessions: {sessions}")
    lines.append(f"Handoff queue (pending): {pending_handoffs}")
    lines.append(f"Unresolved design gates: {unresolved_gates}")
    lines.append(f"Cumulative spend: ${total_cost:.4f}")

    active_total = sum(counts[s] for s in _REPORT_ORDER if s != TaskStatus.BLOCKED)
    blocked = counts[TaskStatus.BLOCKED]
    lines.append(f"Tasks active: {active_total}  blocked: {blocked}")

    nonzero = [s for s in _REPORT_ORDER if counts[s] > 0]
    if nonzero:
        lines.append("By status:")
        for s in nonzero:
            lines.append(f"  • {s.value}: {counts[s]}")

    return "\n".join(lines)
