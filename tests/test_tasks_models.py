from datetime import datetime

import pytest
from pydantic import ValidationError

from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.models import Task, TaskEvent, Gate, HandoffRow


def test_task_minimal_fields():
    t = Task(
        id=1, title="x", description="y",
        status=TaskStatus.PENDING, origin_chat_id=42,
        created_at=datetime(2026, 5, 17), updated_at=datetime(2026, 5, 17),
    )
    assert t.status == TaskStatus.PENDING
    assert t.parent_id is None
    assert t.owner is None


def test_task_rejects_unknown_status():
    with pytest.raises(ValidationError):
        Task(
            id=1, title="x", description="y",
            status="bogus", origin_chat_id=42,
            created_at=datetime(2026, 5, 17), updated_at=datetime(2026, 5, 17),
        )


def test_task_event_kind_required():
    ev = TaskEvent(
        id=1, task_id=1, ts=datetime(2026, 5, 17),
        actor="pm", kind="comment", payload={"body": "hi"},
    )
    assert ev.payload == {"body": "hi"}


def test_gate_resolution_optional():
    g = Gate(
        id=1, task_id=1, kind="design",
        requested_at=datetime(2026, 5, 17),
    )
    assert g.resolution is None
    assert g.resolved_at is None


def test_handoff_row_basic():
    h = HandoffRow(
        id=1, task_id=1, from_agent="pm", to_agent="architect",
        message="hi", enqueued_at=datetime(2026, 5, 17),
    )
    assert h.claimed_at is None
