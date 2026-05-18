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
