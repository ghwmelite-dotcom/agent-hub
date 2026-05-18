"""Single-instance lock for agent_hub via a pidfile.

Prevents two agent_hub processes from racing on the same data/ dir.
On acquire, writes the current PID; if the file already exists and
the recorded PID is still alive, raises LockHeld. Stale lockfiles
(PID dead) are stolen.

Cross-platform PID liveness via psutil.pid_exists().
"""

from __future__ import annotations

import os
from pathlib import Path

import psutil


class LockHeld(RuntimeError):
    """Raised when another agent_hub process holds the lock."""


class OrchestratorLock:
    def __init__(self, path: Path):
        self.path = path
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            existing = self.path.read_text().strip()
            try:
                existing_pid = int(existing)
            except ValueError:
                existing_pid = None
            if existing_pid is not None and psutil.pid_exists(existing_pid):
                raise LockHeld(
                    f"Lock {self.path} held by live PID {existing_pid}. "
                    f"Stop the other agent_hub process or remove the lock file."
                )
            # Stale — fall through to overwrite.
        self.path.write_text(str(os.getpid()))
        self._owned = True

    def release(self) -> None:
        if self._owned and self.path.exists():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._owned = False
