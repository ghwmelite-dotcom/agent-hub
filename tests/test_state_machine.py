import pytest
from agent_hub.state_machine import (
    ALLOWED_TRANSITIONS,
    TaskStatus,
    is_allowed,
    validate_transition,
    InvalidTransition,
)


def test_all_known_statuses_appear_in_map():
    statuses = {s for pair in ALLOWED_TRANSITIONS for s in pair}
    # Plus None (initial state from tasks.create).
    expected = {None, TaskStatus.PENDING, TaskStatus.PLANNING,
                TaskStatus.DESIGN_REVIEW, TaskStatus.READY,
                TaskStatus.IN_PROGRESS, TaskStatus.REVIEW,
                TaskStatus.DONE, TaskStatus.BLOCKED}
    assert expected.issubset(statuses)


def test_initial_creation_is_pending():
    assert is_allowed(None, TaskStatus.PENDING)


def test_pending_to_planning_allowed():
    assert is_allowed(TaskStatus.PENDING, TaskStatus.PLANNING)


def test_pending_to_done_disallowed():
    assert not is_allowed(TaskStatus.PENDING, TaskStatus.DONE)


def test_blocked_reachable_from_any():
    for s in (TaskStatus.PLANNING, TaskStatus.DESIGN_REVIEW,
              TaskStatus.READY, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW):
        assert is_allowed(s, TaskStatus.BLOCKED), f"BLOCKED unreachable from {s}"


def test_blocked_resumes_to_planning():
    assert is_allowed(TaskStatus.BLOCKED, TaskStatus.PLANNING)


def test_review_kickback_to_in_progress():
    assert is_allowed(TaskStatus.REVIEW, TaskStatus.IN_PROGRESS)


def test_design_reject_returns_to_planning():
    assert is_allowed(TaskStatus.DESIGN_REVIEW, TaskStatus.PLANNING)


def test_validate_raises_on_invalid():
    with pytest.raises(InvalidTransition) as exc:
        validate_transition(TaskStatus.PENDING, TaskStatus.DONE)
    assert "PENDING" in str(exc.value)
    assert "DONE" in str(exc.value)


def test_validate_returns_none_on_valid():
    assert validate_transition(TaskStatus.READY, TaskStatus.IN_PROGRESS) is None
