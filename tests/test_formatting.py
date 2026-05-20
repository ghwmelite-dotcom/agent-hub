"""Tests for the Telegram formatting helpers."""

from __future__ import annotations

from agent_hub.telegram_bot.formatting import escape, role_header


def test_escape_dot():
    assert escape("hello.world") == "hello\\.world"


def test_escape_all_reserved_chars():
    text = "_*[]()~`>#+-=|{}.!"
    out = escape(text)
    # Every reserved char must be backslash-prefixed.
    for ch in text:
        assert f"\\{ch}" in out


def test_escape_passes_safe_chars():
    assert escape("abc123 XYZ") == "abc123 XYZ"


def test_role_header_renders():
    assert role_header("Reviewer") == "▍ *Reviewer*\n\n"


def test_role_header_escapes_dots_in_name():
    assert role_header("QA.Bot") == "▍ *QA\\.Bot*\n\n"


# ===== Task 2: humanize_tool() =====

import pytest
from agent_hub.telegram_bot.formatting import humanize_tool


# ---------- Built-in tools ----------

@pytest.mark.parametrize("tool,args,expected", [
    ("Read",  {"file_path": "README.md"},                  "Read README.md"),
    ("Read",  {"file_path": "C:\\dev\\agent-hub\\store.py"}, "Read store.py"),
    ("Read",  {"file_path": "/Users/x/proj/store.py"},     "Read store.py"),
    ("Edit",  {"file_path": "streamer.py"},                "Edit streamer.py"),
    ("Write", {"file_path": "capture.py"},                 "Write capture.py"),
    ("Grep",  {"pattern": "useState"},                     'Grep "useState"'),
    ("Glob",  {"pattern": "**/*.tsx"},                     'Glob "**/*.tsx"'),
])
def test_humanize_builtin_simple(tool, args, expected):
    assert humanize_tool(tool, args) == expected


def test_humanize_bash_short():
    assert humanize_tool("Bash", {"command": "ls -la"}) == "Bash · ls -la"


def test_humanize_bash_truncates_at_60():
    long_cmd = "npm test -- --reporter dot --silent --runInBand --no-coverage"
    out = humanize_tool("Bash", {"command": long_cmd})
    assert out.startswith("Bash · ")
    detail = out[len("Bash · "):]
    # 60 chars + ellipsis
    assert len(detail) == 61
    assert detail.endswith("…")


def test_humanize_bash_strips_to_first_line():
    """Multi-line commands collapse to the first line."""
    cmd = "set -e\nnpm test"
    assert humanize_tool("Bash", {"command": cmd}) == "Bash · set -e"


def test_humanize_websearch():
    assert humanize_tool("WebSearch", {"query": "python-telegram-bot"}) \
        == "Search · python-telegram-bot"


def test_humanize_webfetch():
    assert humanize_tool("WebFetch", {"url": "https://core.telegram.org/bots/api"}) \
        == "Fetch · https://core.telegram.org/bots/api"


def test_humanize_unknown_builtin_returns_name():
    assert humanize_tool("FutureBuiltin", {}) == "FutureBuiltin"


# ---------- MCP tools ----------

@pytest.mark.parametrize("tool,args,expected", [
    ("mcp__agent_hub__tasks_get",     {"task_id": 5},                        "Read task #5"),
    ("mcp__agent_hub__tasks_create",  {"title": "Add hello line"},           'Create task "Add hello line"'),
    ("mcp__agent_hub__tasks_update",  {"task_id": 5, "status": "review"},   "Update task #5 → review"),
    ("mcp__agent_hub__tasks_update",  {"task_id": 5},                       "Update task #5"),
    ("mcp__agent_hub__tasks_comment", {"task_id": 5, "body": "..."},        "Comment on task #5"),
    ("mcp__agent_hub__tasks_list",    {},                                    "List tasks"),
    ("mcp__agent_hub__handoff",       {"to_agent": "reviewer"},             "Hand off to reviewer"),
    ("mcp__agent_hub__gate_request",  {"kind": "design"},                   "Request design gate"),
    ("mcp__agent_hub__worktree_path", {"task_id": 5},                       "Read worktree path"),
    ("mcp__agent_hub__memory_note",   {"type": "project_fact"},             "Record project fact"),
])
def test_humanize_mcp_known(tool, args, expected):
    assert humanize_tool(tool, args) == expected


def test_humanize_mcp_unknown_falls_back():
    assert humanize_tool("mcp__agent_hub__future_thing", {}) == "future.thing"


def test_humanize_truncates_long_detail():
    """All detail (not just Bash) caps at 60 chars + ellipsis."""
    long_title = "X" * 200
    out = humanize_tool("mcp__agent_hub__tasks_create", {"title": long_title})
    assert out.startswith('Create task "')
    # The detail portion (everything after the label) must fit in quote + 60 + ellipsis + quote.
    detail = out[len("Create task "):]
    assert len(detail) <= 63  # opening quote + 60 chars + ellipsis + closing quote
