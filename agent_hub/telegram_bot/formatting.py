"""Formatting helpers for Telegram agent output.

Pure functions only — no I/O, no global state, no Telegram client.
The streamer and bot.py call these to convert agent events into
MarkdownV2-safe strings.

Style: "editorial" — bold role header on the first bubble, compact
italic tool-call lines, prose treated as MarkdownV2 source.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

from telegram.helpers import escape_markdown


def escape(text: str) -> str:
    """Backslash-escape every MarkdownV2 reserved character."""
    return escape_markdown(text, version=2)


def role_header(display_name: str) -> str:
    """First line + blank line that opens an agent's turn.

    Example: role_header("Reviewer") -> "▍ *Reviewer*\\n\\n"
    """
    return f"▍ *{escape(display_name)}*\n\n"


# ---------- Tool humanization ----------

_DETAIL_LIMIT = 60


def _truncate(text: str, limit: int = _DETAIL_LIMIT) -> str:
    """Cut to `limit` chars + ellipsis if longer."""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _basename(path: str) -> str:
    """Last path segment, working for both '/' and '\\' separators."""
    return PurePath(path.replace("\\", "/")).name or path


def humanize_tool(tool: str, args: dict[str, Any]) -> str:
    """Render a single tool invocation as a one-line label.

    Falls back gracefully for unknown tools so new tools never produce
    the raw `mcp__agent_hub__*` string in user-facing chat.
    """
    args = args or {}

    # ---------- Built-in tools ----------
    if tool in ("Read", "Edit", "Write"):
        path = args.get("file_path") or args.get("path") or "?"
        return f"{tool} {_basename(path)}"

    if tool == "Bash":
        cmd_full = (args.get("command") or "").strip().splitlines()
        cmd = cmd_full[0] if cmd_full else ""
        return f"Bash · {_truncate(cmd)}"

    if tool in ("Grep", "Glob"):
        pat = args.get("pattern") or args.get("query") or "?"
        return f'{tool} "{_truncate(pat)}"'

    if tool == "WebSearch":
        q = args.get("query") or "?"
        return f"Search · {_truncate(q)}"

    if tool == "WebFetch":
        u = args.get("url") or "?"
        return f"Fetch · {_truncate(u)}"

    # ---------- MCP tools (mcp__agent_hub__*) ----------
    mcp_label = _humanize_mcp(tool, args)
    if mcp_label is not None:
        return mcp_label

    # ---------- Fallback for everything else ----------
    return tool


def _humanize_mcp(tool: str, args: dict[str, Any]) -> str | None:
    """Known-MCP table + soft fallback. Returns None if `tool` doesn't
    start with the MCP prefix."""
    prefix = "mcp__agent_hub__"
    if not tool.startswith(prefix):
        return None
    suffix = tool[len(prefix):]

    task_id = args.get("task_id")
    title = args.get("title") or ""
    status = args.get("status")
    to_agent = args.get("to_agent") or "?"
    kind = args.get("kind") or "?"

    if suffix == "tasks_get":
        return f"Read task #{task_id}" if task_id is not None else "Read task"
    if suffix == "tasks_create":
        return f'Create task "{_truncate(title)}"'
    if suffix == "tasks_update":
        if status:
            return f"Update task #{task_id} → {status}"
        return f"Update task #{task_id}"
    if suffix == "tasks_comment":
        return f"Comment on task #{task_id}"
    if suffix == "tasks_list":
        return "List tasks"
    if suffix == "handoff":
        return f"Hand off to {to_agent}"
    if suffix == "gate_request":
        return f"Request {kind} gate"
    if suffix == "worktree_path":
        return "Read worktree path"
    if suffix == "memory_note":
        return "Record project fact"

    # Soft fallback: strip prefix, replace _ with ., lowercase.
    return suffix.replace("_", ".").lower()
