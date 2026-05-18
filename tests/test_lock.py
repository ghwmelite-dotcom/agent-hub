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
