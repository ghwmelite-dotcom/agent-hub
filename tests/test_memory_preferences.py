"""Tests for preference-candidate detection."""

from __future__ import annotations

import pytest

from agent_hub.memory.preferences import looks_like_preference


@pytest.mark.parametrize("text,expected", [
    ("don't add code comments", True),
    ("Don't add code comments", True),
    ("dont add code comments", True),  # missing apostrophe
    ("always use prepared statements", True),
    ("never mock the database", True),
    ("stop summarizing what you did", True),
    ("from now on, prefer Drizzle", True),
    ("prefer one bundled PR", True),
    ("please don't squash commits", True),
    ("@pm build me a thing", False),
    ("what is the status?", False),
    ("/approve 42", False),
])
def test_looks_like_preference(text, expected):
    assert looks_like_preference(text) is expected
