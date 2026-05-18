"""Tests that the agent_hub entrypoint acquires the orchestrator lock
before doing any setup. We don't boot the whole bot — we exercise the
lock-acquisition helper directly."""

import os
from pathlib import Path

import pytest

from agent_hub.__main__ import _resolve_lock_path, _acquire_orchestrator_lock_or_exit
from agent_hub.orchestrator.lock import LockHeld


def test_resolve_lock_path_is_alongside_db(tmp_path: Path):
    db_path = tmp_path / "data" / "agent_hub.db"
    lock_path = _resolve_lock_path(db_path)
    assert lock_path == db_path.parent / ".orchestrator.lock"


def test_acquire_lock_succeeds_on_fresh_path(tmp_path: Path):
    db_path = tmp_path / "data" / "agent_hub.db"
    lock = _acquire_orchestrator_lock_or_exit(db_path)
    try:
        assert (tmp_path / "data" / ".orchestrator.lock").exists()
        assert (tmp_path / "data" / ".orchestrator.lock").read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_acquire_lock_raises_when_already_held(tmp_path: Path):
    db_path = tmp_path / "data" / "agent_hub.db"
    first = _acquire_orchestrator_lock_or_exit(db_path)
    try:
        with pytest.raises(LockHeld):
            _acquire_orchestrator_lock_or_exit(db_path)
    finally:
        first.release()


def test_export_db_path_sets_env(tmp_path: Path, monkeypatch):
    """The runner's child MCP processes need AGENT_HUB_DB to find the
    database. The entrypoint exports it before launching anything."""
    from agent_hub.__main__ import _export_db_path_to_env

    db_path = tmp_path / "data" / "agent_hub.db"
    monkeypatch.delenv("AGENT_HUB_DB", raising=False)
    _export_db_path_to_env(db_path)
    assert os.environ["AGENT_HUB_DB"] == str(db_path)
