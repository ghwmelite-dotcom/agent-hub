"""Formatting helpers for Telegram agent output.

Pure functions only — no I/O, no global state, no Telegram client.
The streamer and bot.py call these to convert agent events into
MarkdownV2-safe strings.

Style: "editorial" — bold role header on the first bubble, compact
italic tool-call lines, prose treated as MarkdownV2 source.
"""

from __future__ import annotations

import re
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


# ---------- MarkdownV2 prose tokenizer ----------

# Ordered patterns — match the LONGEST/most-specific markers first.
# Each pattern's group is the whole token including its markers.
_TOKEN_RE = re.compile(
    r"(```[\s\S]*?```)"               # fenced code block (multiline)
    r"|(`[^`\n]+`)"                   # inline code
    r"|(\*\*[^*\n]+\*\*)"             # github bold
    r"|(\*[^*\n]+\*)"                 # markdownv2 bold
    r"|(__[^_\n]+__)"                 # github italic
    r"|(_[^_\n]+_)"                   # markdownv2 italic
    r"|(\|\|[^|\n]+\|\|)"             # spoiler
    r"|(\[[^\]\n]+\]\([^)\n]+\))"     # link
)


def _escape_in_code(text: str) -> str:
    """Inside ` … ` / ``` … ``` only ` and \\ need escaping."""
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _render_token(token: str) -> str:
    """Given a matched markdown token, return its MarkdownV2 form."""
    # Fenced code block
    if token.startswith("```"):
        inner = token[3:-3]
        return "```" + _escape_in_code(inner) + "```"
    # Inline code
    if token.startswith("`"):
        inner = token[1:-1]
        return "`" + _escape_in_code(inner) + "`"
    # GitHub bold → MarkdownV2 bold
    if token.startswith("**"):
        inner = token[2:-2]
        return "*" + escape(inner) + "*"
    # MarkdownV2 bold (already single-*)
    if token.startswith("*"):
        inner = token[1:-1]
        return "*" + escape(inner) + "*"
    # GitHub italic
    if token.startswith("__"):
        inner = token[2:-2]
        return "_" + escape(inner) + "_"
    # MarkdownV2 italic
    if token.startswith("_"):
        inner = token[1:-1]
        return "_" + escape(inner) + "_"
    # Spoiler
    if token.startswith("||"):
        inner = token[2:-2]
        return "||" + escape(inner) + "||"
    # Link [label](url)
    if token.startswith("["):
        m = re.match(r"^\[([^\]]+)\]\(([^)]+)\)$", token)
        if m:
            label, url = m.group(1), m.group(2)
            return f"[{escape(label)}]({url})"
    # Shouldn't reach — fall back to escaping the whole thing as plain.
    return escape(token)


def to_markdownv2(prose: str) -> str:
    """Convert agent prose into MarkdownV2-safe text.

    1. Tokenize using `_TOKEN_RE` to find inline-code, fenced code,
       bold, italic, spoiler, and link markers.
    2. Translate GitHub-style **bold** / __italic__ into MarkdownV2's
       *bold* / _italic_.
    3. Escape every reserved character in plain segments and in the
       inner text of formatted segments (except code, which only
       needs ` and \\ escaped).

    Unmatched markers fall through to the plain-segment escape path
    (so a lone backtick gets escaped, not dropped).
    """
    out: list[str] = []
    last = 0
    for m in _TOKEN_RE.finditer(prose):
        # Plain text between previous token and this one
        if m.start() > last:
            out.append(escape(prose[last:m.start()]))
        token = m.group(0)
        out.append(_render_token(token))
        last = m.end()
    # Trailing plain text after the last token
    if last < len(prose):
        out.append(escape(prose[last:]))
    return "".join(out)
