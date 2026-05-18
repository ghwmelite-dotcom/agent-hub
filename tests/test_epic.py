"""Tests for epic auto-completion — when the last leaf of an epic
transitions to done, mark the epic done."""

import pytest

from agent_hub.db import Database
from agent_hub.orchestrator.epic import maybe_complete_epic
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), db


async def _advance_to_done(repo: TaskRepository, task_id: int) -> None:
    await repo.update(task_id, status=TaskStatus.PLANNING)
    await repo.update(task_id, status=TaskStatus.IN_PROGRESS)
    await repo.update(task_id, status=TaskStatus.REVIEW)
    await repo.update(task_id, status=TaskStatus.DONE)


@pytest.mark.asyncio
async def test_completes_epic_when_last_leaf_done(deps):
    repo, db = deps
    epic = await repo.create(title="billing", description="-", origin_chat_id=1)
    await repo.update(epic.id, status=TaskStatus.PLANNING)
    a = await repo.create(title="a", description="-", origin_chat_id=1, parent_id=epic.id)
    b = await repo.create(title="b", description="-", origin_chat_id=1, parent_id=epic.id)

    await _advance_to_done(repo, a.id)
    completed = await maybe_complete_epic(task_id=a.id, db_path=db.path)
    assert completed is None  # b still open

    await _advance_to_done(repo, b.id)
    completed = await maybe_complete_epic(task_id=b.id, db_path=db.path)

    assert completed == epic.id
    fresh_epic = await repo.get(epic.id)
    assert fresh_epic.status == TaskStatus.DONE


@pytest.mark.asyncio
async def test_root_task_returns_none(deps):
    repo, db = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    result = await maybe_complete_epic(task_id=t.id, db_path=db.path)
    assert result is None


@pytest.mark.asyncio
async def test_already_done_epic_is_noop(deps):
    repo, db = deps
    epic = await repo.create(title="e", description="-", origin_chat_id=1)
    await _advance_to_done(repo, epic.id)
    leaf = await repo.create(title="l", description="-", origin_chat_id=1, parent_id=epic.id)
    await _advance_to_done(repo, leaf.id)

    result = await maybe_complete_epic(task_id=leaf.id, db_path=db.path)
    assert result is None
