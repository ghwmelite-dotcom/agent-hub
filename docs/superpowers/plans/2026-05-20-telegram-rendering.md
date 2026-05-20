# Telegram Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current raw-markdown-leaking Telegram output with an editorial style: bold role header on the first bubble, humanized tool calls as compact italic lines, and `parse_mode=MarkdownV2` so the agent's bold/italic/code actually renders.

**Architecture:** One new file (`agent_hub/telegram_bot/formatting.py`) with four pure functions: `escape`, `role_header`, `humanize_tool`, `to_markdownv2`. The existing `streamer.py` gains a `parse_mode` field with a one-shot `parse_mode=None` fall-back when Telegram rejects MarkdownV2 parsing. `bot.py:_render_event` routes prose through `to_markdownv2` and tool events through `humanize_tool`, deletes the old `_summarize_tool`.

**Tech Stack:** Python 3.12, python-telegram-bot 22.x (already installed; `telegram.helpers.escape_markdown(version=2)` available), pytest + pytest-asyncio (existing patterns).

**Spec:** `docs/superpowers/specs/2026-05-20-telegram-rendering-design.md`

---

## File Structure

**New files:**
- `agent_hub/telegram_bot/formatting.py` — 4 pure functions + tool name table
- `tests/test_formatting.py` — pure-function tests
- `tests/test_streamer_parse_mode.py` — 3 tests against a stub Bot

**Modified files:**
- `agent_hub/telegram_bot/streamer.py` — `parse_mode` field, send_message/edit_text propagation, parse-error retry
- `agent_hub/telegram_bot/bot.py` — `_render_event` routing, role_header build, drop `_summarize_tool`
- `tests/test_surface_telegram.py` — one end-to-end render test

---

## Task 1: Helpers — `escape()` and `role_header()`

**Files:**
- Create: `agent_hub/telegram_bot/formatting.py`
- Test: `tests/test_formatting.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_formatting.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_formatting.py -v`
Expected: ImportError or 5 failures — module doesn't exist.

- [ ] **Step 3: Implement helpers**

Create `agent_hub/telegram_bot/formatting.py`:

```python
"""Formatting helpers for Telegram agent output.

Pure functions only — no I/O, no global state, no Telegram client.
The streamer and bot.py call these to convert agent events into
MarkdownV2-safe strings.

Style: "editorial" — bold role header on the first bubble, compact
italic tool-call lines, prose treated as MarkdownV2 source.
"""

from __future__ import annotations

from telegram.helpers import escape_markdown


def escape(text: str) -> str:
    """Backslash-escape every MarkdownV2 reserved character."""
    return escape_markdown(text, version=2)


def role_header(display_name: str) -> str:
    """First line + blank line that opens an agent's turn.

    Example: role_header("Reviewer") -> "▍ *Reviewer*\\n\\n"
    """
    return f"▍ *{escape(display_name)}*\n\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_formatting.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/telegram_bot/formatting.py tests/test_formatting.py
git commit -m "feat(telegram): formatting helpers — escape + role_header"
```

---

## Task 2: `humanize_tool()` — built-in and MCP tools

**Files:**
- Modify: `agent_hub/telegram_bot/formatting.py`
- Test: `tests/test_formatting.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_formatting.py`:

```python
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
    # The detail portion (everything after the label) must fit in 60+1 chars.
    detail = out[len("Create task "):]
    assert len(detail) <= 62  # opening quote + 60 chars + ellipsis (closing quote may be dropped)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_formatting.py -v -k humanize`
Expected: import error (`humanize_tool` not defined).

- [ ] **Step 3: Implement `humanize_tool`**

Append to `agent_hub/telegram_bot/formatting.py`:

```python
from pathlib import PurePath
from typing import Any


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_formatting.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/telegram_bot/formatting.py tests/test_formatting.py
git commit -m "feat(telegram): humanize_tool for built-in + MCP tools"
```

---

## Task 3: `to_markdownv2()` — agent prose tokenizer

**Files:**
- Modify: `agent_hub/telegram_bot/formatting.py`
- Test: `tests/test_formatting.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_formatting.py`:

```python
from agent_hub.telegram_bot.formatting import to_markdownv2


def test_to_markdownv2_escapes_plain_text():
    assert to_markdownv2("Done. Tests pass!") == "Done\\. Tests pass\\!"


def test_to_markdownv2_preserves_inline_code():
    out = to_markdownv2("The `foo.bar` function exists.")
    # Text outside the code span gets escaped, code span survives intact.
    assert "`foo.bar`" in out
    assert "function exists\\." in out


def test_to_markdownv2_preserves_fenced_code_block():
    src = "before\n```python\nprint('hi.')\n```\nafter."
    out = to_markdownv2(src)
    # The code-block contents survive unescaped (only ` and \ need escaping inside).
    assert "```python\nprint('hi.')\n```" in out
    assert "before" in out
    assert "after\\." in out


def test_to_markdownv2_translates_github_bold():
    """`**bold**` becomes `*bold*` (MarkdownV2 single-asterisk bold)."""
    out = to_markdownv2("This is **important** text.")
    assert "*important*" in out
    assert "**" not in out


def test_to_markdownv2_translates_github_italic():
    out = to_markdownv2("Slightly __emphasis__ here.")
    assert "_emphasis_" in out
    assert "__" not in out


def test_to_markdownv2_preserves_native_bold():
    """A MarkdownV2-style `*bold*` is left as-is."""
    out = to_markdownv2("Already *bold* here.")
    assert "*bold*" in out


def test_to_markdownv2_preserves_native_italic():
    out = to_markdownv2("Already _italic_ here.")
    assert "_italic_" in out


def test_to_markdownv2_preserves_spoiler():
    out = to_markdownv2("Hidden ||spoiler|| here.")
    assert "||spoiler||" in out


def test_to_markdownv2_preserves_link():
    out = to_markdownv2("See [docs](https://example.com) for details.")
    assert "[docs](https://example.com)" in out


def test_to_markdownv2_unbalanced_markers_fall_through_as_plain():
    """A lone backtick or asterisk gets escaped, not silently dropped."""
    out = to_markdownv2("a `b c")
    # No closing backtick → treat the backtick as plain → must be escaped.
    assert "\\`" in out


def test_to_markdownv2_mixed_content():
    """Prose with one code span, one bold, one plain sentence."""
    src = "Check `s.isSlowEffective()` not **s.isSlow**. Done."
    out = to_markdownv2(src)
    assert "`s.isSlowEffective()`" in out
    assert "*s\\.isSlow*" in out
    assert "Done\\." in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_formatting.py -v -k markdownv2`
Expected: import error.

- [ ] **Step 3: Implement `to_markdownv2`**

Append to `agent_hub/telegram_bot/formatting.py`:

```python
import re


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_formatting.py -v -k markdownv2`
Expected: all 11 markdownv2 tests pass.

- [ ] **Step 5: Run the whole formatting test file**

Run: `pytest tests/test_formatting.py -v`
Expected: every formatting test passes (no regressions in Tasks 1-2).

- [ ] **Step 6: Commit**

```bash
git add agent_hub/telegram_bot/formatting.py tests/test_formatting.py
git commit -m "feat(telegram): to_markdownv2 prose tokenizer + escaper"
```

---

## Task 4: Streamer — `parse_mode` field + parse-error fallback

**Files:**
- Modify: `agent_hub/telegram_bot/streamer.py`
- Test: `tests/test_streamer_parse_mode.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_streamer_parse_mode.py`:

```python
"""Tests for StreamingMessage's parse_mode handling and fallback."""

from __future__ import annotations

import asyncio

import pytest
from telegram.error import BadRequest

from agent_hub.telegram_bot.streamer import StreamingMessage


class _FakeMessage:
    """Stand-in for telegram.Message that records edit calls."""

    def __init__(self, message_id: int, bot: "_FakeBot"):
        self.message_id = message_id
        self.bot = bot
        self.edits: list[dict] = []
        self._fail_next_parse: bool = False

    async def edit_text(self, text: str, **kwargs):
        if self._fail_next_parse and kwargs.get("parse_mode") == "MarkdownV2":
            self._fail_next_parse = False
            raise BadRequest("can't parse entities: bad escape")
        self.edits.append({"text": text, **kwargs})


class _FakeBot:
    def __init__(self):
        self.sent: list[dict] = []
        self._next_id = 1
        self.next_message_fails_parse: bool = False

    async def send_message(self, chat_id: int, text: str, **kwargs):
        msg = _FakeMessage(self._next_id, self)
        self._next_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        if self.next_message_fails_parse:
            msg._fail_next_parse = True
            self.next_message_fails_parse = False
        return msg


@pytest.mark.asyncio
async def test_send_initial_uses_markdownv2():
    bot = _FakeBot()
    stream = StreamingMessage(chat_id=42, bot=bot, prefix="▍ *Hi*\n\n")
    await stream.append("hello world")
    # The first send_message call must have parse_mode set.
    assert bot.sent[0]["parse_mode"] == "MarkdownV2"


@pytest.mark.asyncio
async def test_safe_edit_falls_back_to_plain_on_parse_error(monkeypatch):
    """When MarkdownV2 parsing fails on an edit, retry once without parse_mode."""
    bot = _FakeBot()
    stream = StreamingMessage(chat_id=42, bot=bot, prefix="hdr ")
    await stream.append("first")           # Triggers send_initial
    msg = stream.current_message
    msg._fail_next_parse = True            # Next edit will raise BadRequest

    # Force the throttle window to expire so _flush actually edits.
    stream.last_edit_at = 0
    await stream.append(" second")

    # The retry should have landed: there must be at least one edit
    # recorded, and the latest one must NOT have parse_mode=MarkdownV2.
    assert msg.edits, "expected a retry edit after the parse failure"
    assert msg.edits[-1].get("parse_mode") is None


@pytest.mark.asyncio
async def test_continuation_bubble_omits_role_header():
    """When text overflows 4096 chars, the new bubble's send_message
    must not include the prefix (role header) — only the overflow text."""
    bot = _FakeBot()
    prefix = "▍ *Pm*\n\n"
    stream = StreamingMessage(chat_id=42, bot=bot, prefix=prefix)
    big = "x" * 5000          # > TELEGRAM_MAX_CHARS (4000)
    await stream.append(big)
    stream.last_edit_at = 0   # allow flush
    await stream.append("y")  # trigger flush + overflow

    # First send_message carries the prefix; second one does NOT.
    assert bot.sent[0]["text"].startswith(prefix)
    assert len(bot.sent) >= 2
    assert not bot.sent[1]["text"].startswith(prefix)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_streamer_parse_mode.py -v`
Expected: failures — `parse_mode` not set, no fallback logic exists.

- [ ] **Step 3: Modify `agent_hub/telegram_bot/streamer.py`**

Add `import re` near the top (alongside `import asyncio` / `import time`).

Add a `parse_mode` field on the dataclass. Add `parse_mode` to `send_message` / `edit_text` calls. Add the BadRequest fallback in `_safe_edit`.

Replace the contents of `agent_hub/telegram_bot/streamer.py` with:

```python
"""Telegram message streaming with throttled edits.

Telegram's Bot API rate-limits message edits to roughly one per second per
chat. We accumulate text from the agent and edit the message at most every
1.5 seconds. When the accumulated text exceeds the per-message character
limit (4096), we send a new message and continue there.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from telegram import Message
from telegram.error import BadRequest, RetryAfter, TelegramError

TELEGRAM_MAX_CHARS = 4000  # Slightly under the 4096 hard limit for safety.
EDIT_INTERVAL_SECONDS = 1.5

# Telegram returns this class of error when MarkdownV2 escaping is wrong.
# We retry once with parse_mode=None so a bad escape never blocks the stream.
_PARSE_FAILURE_RE = re.compile(r"can'?t parse entities|can'?t find end",
                               re.IGNORECASE)


@dataclass
class StreamingMessage:
    """One streamed reply from an agent, possibly spanning several Telegram
    messages once the text crosses the per-message length cap."""

    chat_id: int
    bot: object  # telegram.Bot, kept loose to avoid heavy type imports here
    prefix: str = ""
    parse_mode: str | None = "MarkdownV2"
    current_message: Message | None = None
    current_text: str = ""
    last_edit_at: float = 0.0
    pending_text: str = ""
    sent_messages: list[Message] = field(default_factory=list)

    async def append(self, chunk: str) -> None:
        """Add a chunk of text and edit/post as needed."""
        if not chunk:
            return

        self.pending_text += chunk
        now = time.monotonic()

        if self.current_message is None:
            await self._send_initial()
            return

        if now - self.last_edit_at < EDIT_INTERVAL_SECONDS:
            return

        await self._flush()

    async def finalize(self) -> None:
        """Force-flush any pending text. Call when the agent's turn ends."""
        if self.pending_text:
            await self._flush(force=True)

    # ------------------------------------------------------------------

    async def _send_initial(self) -> None:
        text = (self.prefix + self.pending_text)[:TELEGRAM_MAX_CHARS]
        self.current_message = await self.bot.send_message(  # type: ignore[attr-defined]
            chat_id=self.chat_id,
            text=text or self.prefix or "…",
            parse_mode=self.parse_mode,
        )
        self.sent_messages.append(self.current_message)
        self.current_text = text
        self.pending_text = self.pending_text[len(text) - len(self.prefix):]
        self.last_edit_at = time.monotonic()

    async def _flush(self, force: bool = False) -> None:
        assert self.current_message is not None

        new_text = self.current_text + self.pending_text
        if len(new_text) > TELEGRAM_MAX_CHARS:
            cutoff = _natural_split(new_text, TELEGRAM_MAX_CHARS)
            keep = new_text[:cutoff]
            overflow = new_text[cutoff:]
            await self._safe_edit(keep)
            self.current_text = keep
            self.pending_text = ""

            # Continuation bubble — no prefix repetition.
            self.current_message = await self.bot.send_message(  # type: ignore[attr-defined]
                chat_id=self.chat_id,
                text=overflow[:TELEGRAM_MAX_CHARS] or "…",
                parse_mode=self.parse_mode,
            )
            self.sent_messages.append(self.current_message)
            self.current_text = overflow[:TELEGRAM_MAX_CHARS]
            self.pending_text = overflow[TELEGRAM_MAX_CHARS:]
            self.last_edit_at = time.monotonic()
            return

        await self._safe_edit(new_text)
        self.current_text = new_text
        self.pending_text = ""
        self.last_edit_at = time.monotonic()

    async def _safe_edit(self, text: str) -> None:
        assert self.current_message is not None
        try:
            await self.current_message.edit_text(
                text=text, parse_mode=self.parse_mode,
            )
        except RetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 0.1)
            try:
                await self.current_message.edit_text(
                    text=text, parse_mode=self.parse_mode,
                )
            except TelegramError:
                pass
        except BadRequest as exc:
            # MarkdownV2 parse error → one-shot retry with parse_mode=None
            # so a bad escape doesn't block the stream.
            if self.parse_mode and _PARSE_FAILURE_RE.search(str(exc)):
                try:
                    await self.current_message.edit_text(
                        text=text, parse_mode=None,
                    )
                except TelegramError:
                    pass
            # else: "Message is not modified" or similar — ignore.
        except TelegramError:
            pass


def _natural_split(text: str, max_len: int) -> int:
    """Find a nice place to break text near max_len — paragraph > sentence > word."""
    window = text[: max_len + 1]
    for sep in ("\n\n", "\n", ". ", " "):
        idx = window.rfind(sep)
        if idx > max_len // 2:
            return idx + len(sep)
    return max_len
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest tests/test_streamer_parse_mode.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the existing bot tests to verify no regression**

Run: `pytest tests/test_surface_telegram.py tests/test_commands_memory.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/telegram_bot/streamer.py tests/test_streamer_parse_mode.py
git commit -m "feat(telegram): StreamingMessage parse_mode field + parse-error fallback"
```

---

## Task 5: bot.py integration — route text/tools through formatters

**Files:**
- Modify: `agent_hub/telegram_bot/bot.py`
- Test: existing tests must still pass; an end-to-end render test is added in Task 6.

- [ ] **Step 1: Read the current `_render_event` and `_summarize_tool`**

Read `agent_hub/telegram_bot/bot.py` lines 305-394. Identify the exact block to change.

- [ ] **Step 2: Update the stream construction and `_render_event`**

In `agent_hub/telegram_bot/bot.py`:

**Replace** the existing `stream = StreamingMessage(...)` block (currently around line 314-318):

```python
                stream = StreamingMessage(
                    chat_id=chat_id,
                    bot=context.bot,
                    prefix=f"*{display_name}*\n",
                )
```

**with:**

```python
                from agent_hub.telegram_bot.formatting import role_header
                stream = StreamingMessage(
                    chat_id=chat_id,
                    bot=context.bot,
                    prefix=role_header(display_name),
                )
```

**Replace** `_render_event` (currently lines 363-376):

```python
async def _render_event(stream: StreamingMessage, event: AgentEvent) -> None:
    if isinstance(event, TextChunk):
        await stream.append(event.text)
    elif isinstance(event, ToolStart):
        # Short inline indicator so the user sees activity.
        hint = _summarize_tool(event.tool, event.input)
        await stream.append(f"\n_{hint}_\n")
    elif isinstance(event, ToolEnd):
        if event.is_error:
            await stream.append("\n_(tool failed)_\n")
    elif isinstance(event, AgentError):
        await stream.append(f"\n⚠️ {event.message}\n")
    elif isinstance(event, TurnDone):
        pass  # Could surface cost/time here later.
```

**with:**

```python
async def _render_event(stream: StreamingMessage, event: AgentEvent) -> None:
    from agent_hub.telegram_bot.formatting import (
        escape, humanize_tool, to_markdownv2,
    )

    if isinstance(event, TextChunk):
        await stream.append(to_markdownv2(event.text))
    elif isinstance(event, ToolStart):
        label = humanize_tool(event.tool, event.input)
        await stream.append(f"\n_› {escape(label)}_\n")
    elif isinstance(event, ToolEnd):
        if event.is_error:
            await stream.append("\n_› failed_\n")
    elif isinstance(event, AgentError):
        await stream.append(f"\n*⚠ {escape(event.message)}*\n")
    elif isinstance(event, TurnDone):
        pass  # Could surface cost/time here later.
```

**Delete** the existing `_summarize_tool` function (currently lines 379-393):

```python
def _summarize_tool(tool: str, args: dict) -> str:
    """Compact one-line description of a tool invocation for streaming UI."""
    if tool in {"Read", "Edit", "Write"}:
        path = args.get("file_path") or args.get("path") or "?"
        return f"{tool} {path}"
    if tool == "Bash":
        cmd = (args.get("command") or "").strip().splitlines()[0:1]
        return f"$ {cmd[0] if cmd else ''}"[:80]
    if tool in {"Grep", "Glob"}:
        pat = args.get("pattern") or args.get("query") or "?"
        return f"{tool} {pat}"
    if tool in {"WebSearch", "WebFetch"}:
        q = args.get("query") or args.get("url") or "?"
        return f"{tool} {q}"
    return tool
```

(Remove the entire function. `humanize_tool` from `formatting.py` replaces it.)

- [ ] **Step 3: Run the existing test suites to verify no regression**

Run: `pytest tests/ -q -x --ignore=tests/smoke`
Expected: all pass.

- [ ] **Step 4: Spot-check the imports**

Run:
```bash
grep -n "_summarize_tool" agent_hub/telegram_bot/bot.py
```

Expected: no matches (function and all callers are gone).

- [ ] **Step 5: Commit**

```bash
git add agent_hub/telegram_bot/bot.py
git commit -m "feat(telegram): _render_event uses formatting module; drop _summarize_tool"
```

---

## Task 6: End-to-end render test

**Files:**
- Modify: `tests/test_surface_telegram.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_surface_telegram.py`:

```python
@pytest.mark.asyncio
async def test_render_event_produces_editorial_markdownv2():
    """End-to-end: simulate an agent's event sequence and verify the
    accumulated text on the streamed message matches the expected
    MarkdownV2 layout (header, prose, tool line, more prose)."""
    from agent_hub.agents.runner import TextChunk, ToolStart, ToolEnd
    from agent_hub.telegram_bot.bot import _render_event
    from agent_hub.telegram_bot.formatting import role_header
    from agent_hub.telegram_bot.streamer import StreamingMessage

    class _FakeMessage:
        def __init__(self):
            self.text = ""
            self.parse_mode = None

        async def edit_text(self, text: str, **kwargs):
            self.text = text
            self.parse_mode = kwargs.get("parse_mode")

    class _FakeBot:
        def __init__(self):
            self.last_message = None
            self.last_parse_mode = None

        async def send_message(self, chat_id, text, **kwargs):
            msg = _FakeMessage()
            msg.text = text
            msg.parse_mode = kwargs.get("parse_mode")
            self.last_message = msg
            self.last_parse_mode = kwargs.get("parse_mode")
            return msg

    bot = _FakeBot()
    stream = StreamingMessage(
        chat_id=42, bot=bot, prefix=role_header("Reviewer"),
    )

    # Disable throttling so each event flushes immediately.
    async def _flush_now(stream, event):
        stream.last_edit_at = 0
        await _render_event(stream, event)

    await _flush_now(stream, TextChunk(text="All three fixes verified. "))
    await _flush_now(stream, ToolStart(
        tool="Read", input={"file_path": "DigestFeatured.tsx"},
    ))
    await _flush_now(stream, ToolEnd(tool="Read", is_error=False))
    await _flush_now(stream, TextChunk(text="**Blocker 1**: `s.isSlow` is replaced."))
    await stream.finalize()

    # The bubble's text should contain:
    # - Role header (escaped, MarkdownV2)
    # - Escaped prose
    # - Italic tool line with humanized name
    # - GitHub-style **bold** translated to *bold*
    final = stream.current_message.text
    assert "▍ *Reviewer*" in final
    assert "All three fixes verified\\." in final
    assert "_› Read DigestFeatured\\.tsx_" in final
    assert "*Blocker 1*" in final
    assert "`s.isSlow`" in final
    # parse_mode must have been MarkdownV2 on the send_message call.
    assert bot.last_parse_mode == "MarkdownV2"
```

- [ ] **Step 2: Run the test to verify it fails (initially) or passes**

Run: `pytest tests/test_surface_telegram.py -v -k editorial`
Expected: PASS (Task 5 already wired everything). If it fails, the failure shows what's wrong with the integration.

- [ ] **Step 3: Run the full suite once to catch any regression**

Run: `pytest tests/ -q --ignore=tests/smoke`
Expected: every test passes.

- [ ] **Step 4: Commit**

```bash
git add tests/test_surface_telegram.py
git commit -m "test(telegram): end-to-end editorial render assertion"
```

---

## Final verification

```bash
cd C:\dev\agent-hub
.venv\Scripts\python.exe -m pytest -q --ignore=tests/smoke
```

Expected: full suite passes.

Optional manual sanity in Telegram (recommended but not required):

1. Start the bot: `.venv\Scripts\python.exe -m agent_hub`
2. File a task and watch the architect reply. Verify:
   - The first bubble of each agent turn starts with `▍ Reviewer` (or whichever role).
   - Tool calls appear as compact italic lines like `› Read README.md`, `› Bash · npm test`.
   - Agent prose with `**bold**` actually renders bold.
   - Code in backticks renders as `inline code`.
   - No raw `_mcp__agent_hub__*` strings anywhere.
