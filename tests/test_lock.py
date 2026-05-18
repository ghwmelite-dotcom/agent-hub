import os
from pathlib import Path

import pytest

from agent_hub.orchestrator.lock import OrchestratorLock, LockHeld


def test_acquire_writes_pid_to_file(tmp_path: Path):
    lock_path = tmp_path / ".orchestrator.lock"
    lock = OrchestratorLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.exists()
        content = lock_path.read_text().strip()
        assert content == str(os.getpid())
    finally:
        lock.release()


def test_acquire_refuses_when_live_pid_holds_it(tmp_path: Path):
    """If the lock exists and contains a live PID, acquire must raise."""
    lock_path = tmp_path / ".orchestrator.lock"
    # Simulate another agent_hub holding the lock — write our own pid
    # since it's guaranteed live.
    lock_path.write_text(str(os.getpid()))

    lock = OrchestratorLock(lock_path)
    with pytest.raises(LockHeld) as exc:
        lock.acquire()
    assert str(os.getpid()) in str(exc.value)


def test_acquire_steals_when_stale_pid(tmp_path: Path, monkeypatch):
    """If the recorded PID is dead, the lock is stolen."""
    lock_path = tmp_path / ".orchestrator.lock"
    lock_path.write_text("999999999")  # vanishingly unlikely to be alive
    monkeypatch.setattr("psutil.pid_exists", lambda pid: False)  # force dead

    lock = OrchestratorLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_acquire_steals_when_garbage_contents(tmp_path: Path):
    """If the lock file is unreadable as a PID, steal it (treat as stale)."""
    lock_path = tmp_path / ".orchestrator.lock"
    lock_path.write_text("not-a-number")

    lock = OrchestratorLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()
