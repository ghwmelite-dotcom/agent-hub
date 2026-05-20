"""Tests for Event dataclasses + JSON serialization."""

from __future__ import annotations

import json

import pytest

from agent_hub.dashboard.events import (
    Event,
    TaskChanged,
    TaskEvent,
    GateChanged,
    WorkspaceChanged,
    to_json,
)


def test_task_changed_serializes():
    evt = TaskChanged(
        workspace=r"C:\dev\foo",
        task={"id": 7, "title": "t", "status": "running"},
    )
    payload = json.loads(to_json(evt))
    assert payload == {
        "kind": "task_changed",
        "workspace": r"C:\dev\foo",
        "task": {"id": 7, "title": "t", "status": "running"},
    }


def test_task_event_serializes():
    evt = TaskEvent(
        workspace=r"C:\dev\foo",
        task_id=7,
        event={"id": 42, "actor": "fullstack", "kind": "comment", "body": "ok"},
    )
    payload = json.loads(to_json(evt))
    assert payload["kind"] == "task_event"
    assert payload["task_id"] == 7
    assert payload["event"]["actor"] == "fullstack"


def test_gate_changed_serializes():
    evt = GateChanged(
        workspace=r"C:\dev\foo",
        gate={"id": 4, "task_id": 6, "kind": "design", "resolved_at": None},
    )
    payload = json.loads(to_json(evt))
    assert payload["kind"] == "gate_changed"
    assert payload["gate"]["task_id"] == 6


def test_workspace_changed_serializes():
    evt = WorkspaceChanged(workspace=r"C:\dev\foo")
    payload = json.loads(to_json(evt))
    assert payload == {"kind": "workspace_changed", "workspace": r"C:\dev\foo"}


def test_event_is_a_union_type():
    """All four event types are accepted where Event is annotated."""
    events: list[Event] = [
        TaskChanged(workspace="w", task={}),
        TaskEvent(workspace="w", task_id=1, event={}),
        GateChanged(workspace="w", gate={}),
        WorkspaceChanged(workspace="w"),
    ]
    assert len(events) == 4
