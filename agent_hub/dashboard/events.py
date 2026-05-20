"""Event dataclasses for the dashboard pub/sub broker.

Pure data shapes — no I/O, no state, no Telegram or aiohttp imports.
The broker publishes these; the server serializes them to JSON for SSE
delivery to the browser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Union


@dataclass(frozen=True, slots=True)
class TaskChanged:
    """A `tasks` row was created or updated."""
    workspace: str
    task: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """A `task_events` row landed (comment, tool use, handoff, etc.)."""
    workspace: str
    task_id: int
    event: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GateChanged:
    """A `gates` row was requested, resolved, or notified."""
    workspace: str
    gate: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WorkspaceChanged:
    """The active workspace switched. Clients should re-fetch /api/state."""
    workspace: str


Event = Union[TaskChanged, TaskEvent, GateChanged, WorkspaceChanged]


_KIND_BY_TYPE: dict[type, str] = {
    TaskChanged: "task_changed",
    TaskEvent: "task_event",
    GateChanged: "gate_changed",
    WorkspaceChanged: "workspace_changed",
}


def to_json(event: Event) -> str:
    """Serialize an event to a JSON string for SSE delivery.

    The wire format is `{"kind": <kind>, ...rest}` where `<kind>` lets
    the browser dispatch on the message type. The remaining fields are
    the dataclass's field/value pairs as-is.
    """
    kind = _KIND_BY_TYPE.get(type(event))
    if kind is None:
        raise TypeError(f"unknown event type: {type(event).__name__}")
    payload = {"kind": kind, **asdict(event)}
    return json.dumps(payload, default=str)
