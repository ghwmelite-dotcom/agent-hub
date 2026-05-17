"""Shared fixtures for the agent_hub test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """A fresh sqlite file path under tmp_path. Not opened — tests open it themselves."""
    return tmp_path / "agent_hub.db"
