"""Task status enum, allowed-transition map, and validator.

The map is data — every transition the system performs MUST pass through
validate_transition(). Tests cover both allowed and disallowed cases
exhaustively.
"""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    DESIGN_REVIEW = "design_review"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"


# Allowed transitions as (from, to) pairs. `None` means "no prior status"
# (i.e. the initial create).
ALLOWED_TRANSITIONS: frozenset[tuple[TaskStatus | None, TaskStatus]] = frozenset({
    (None, TaskStatus.PENDING),
    (TaskStatus.PENDING, TaskStatus.PLANNING),
    (TaskStatus.PLANNING, TaskStatus.DESIGN_REVIEW),
    (TaskStatus.PLANNING, TaskStatus.IN_PROGRESS),  # small tasks that skip the architect
    (TaskStatus.DESIGN_REVIEW, TaskStatus.READY),   # /approve
    (TaskStatus.DESIGN_REVIEW, TaskStatus.PLANNING),  # /reject — back to planning
    (TaskStatus.READY, TaskStatus.IN_PROGRESS),
    (TaskStatus.IN_PROGRESS, TaskStatus.REVIEW),
    (TaskStatus.REVIEW, TaskStatus.DONE),
    (TaskStatus.REVIEW, TaskStatus.IN_PROGRESS),    # reviewer kick-back
    # Any → blocked (enumerated)
    (TaskStatus.PENDING, TaskStatus.BLOCKED),
    (TaskStatus.PLANNING, TaskStatus.BLOCKED),
    (TaskStatus.DESIGN_REVIEW, TaskStatus.BLOCKED),
    (TaskStatus.READY, TaskStatus.BLOCKED),
    (TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED),
    (TaskStatus.REVIEW, TaskStatus.BLOCKED),
    # Resume from blocked routes to PM via planning
    (TaskStatus.BLOCKED, TaskStatus.PLANNING),
})


class InvalidTransition(ValueError):
    """Raised when a status transition is not in ALLOWED_TRANSITIONS."""


def is_allowed(from_status: TaskStatus | None, to_status: TaskStatus) -> bool:
    return (from_status, to_status) in ALLOWED_TRANSITIONS


def validate_transition(from_status: TaskStatus | None, to_status: TaskStatus) -> None:
    if not is_allowed(from_status, to_status):
        from_label = from_status.name if from_status else "NONE"
        raise InvalidTransition(
            f"Invalid status transition: {from_label} -> {to_status.name}"
        )
