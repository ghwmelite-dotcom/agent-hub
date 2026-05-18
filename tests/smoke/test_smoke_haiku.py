"""End-to-end smoke test with a real Haiku-pinned agent set.

Gated behind RUN_SMOKE_TESTS=1 because it:
- Makes real Claude API calls (~$0.10–0.50 per run)
- Takes 30s–2min to complete
- Requires ANTHROPIC_API_KEY

Run manually before tagging a release:
    set RUN_SMOKE_TESTS=1
    .\\.venv\\Scripts\\python.exe -m pytest tests/smoke/ -v -s
"""

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SMOKE_TESTS") != "1",
    reason="set RUN_SMOKE_TESTS=1 to run smoke tests (real Claude API calls)",
)


def test_skeleton():
    """Sanity check that the gate works."""
    assert True
