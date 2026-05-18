"""Pydantic models for task rows and event payloads.

These mirror the SQLite schema in agent_hub.db. Repositories convert
between rows (tuples) and these models at the boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent_hub.state_machine import TaskStatus


class Task(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    id: int
    parent_id: int | None = None
    title: str
    description: str
    status: TaskStatus
    owner: str | None = None
    worktree_path: str | None = None
    branch_name: str | None = None
    origin_chat_id: int
    created_at: datetime
    updated_at: datetime


class TaskEvent(BaseModel):
    """Event row from task_events table.

    Note: the DB column is ``payload_json TEXT NOT NULL`` storing a JSON
    string; the model's ``payload`` field is the deserialized dict. The
    repository is responsible for ``json.loads`` on read and
    ``json.dumps`` on write — do NOT construct ``TaskEvent`` directly
    from a raw row dict.
    """

    id: int
    task_id: int
    ts: datetime
    actor: str
    kind: str  # comment | status_change | handoff | gate_request | gate_resolve | push | error
    payload: dict[str, Any]


class Gate(BaseModel):
    id: int
    task_id: int
    kind: str  # "design" in v1
    artifact_path: str | None = None
    summary: str | None = None
    requested_at: datetime
    resolved_at: datetime | None = None
    resolution: str | None = None  # approved | rejected | None


class HandoffRow(BaseModel):
    id: int
    task_id: int
    from_agent: str
    to_agent: str
    message: str
    enqueued_at: datetime
    claimed_at: datetime | None = None


class Worktree(BaseModel):
    """Row from the worktrees table.

    One row per task, keyed on task_id (PK). cleaned_at is set when
    the worktree is removed from disk after the task is done.
    """
    task_id: int
    path: str
    branch: str
    base_branch: str
    created_at: datetime
    cleaned_at: datetime | None = None
