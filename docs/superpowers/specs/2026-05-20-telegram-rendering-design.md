# Telegram Rendering for Agent Output

**Status:** Design
**Date:** 2026-05-20
**Author:** brainstorming session

## Problem

The Telegram bot currently sends agent output as plain text with `parse_mode=None`. Three failures fall out of that:

1. Agents emit GitHub-flavored markdown (`**Blocker 1: `s.isSlow` → `s.isSlowEffective()`**`). The bot doesn't tell Telegram to parse it, so the literal asterisks and backticks land in the chat unrendered.
2. Tool calls are summarized with raw, ugly names: `_mcp__agent_hub__tasks_get_`. The `mcp__agent_hub__` prefix is internal plumbing the end user shouldn't see.
3. The agent's prose and the tool-call lines have no visual hierarchy. Long reviewer/architect turns read as a wall of monospaced asterisks and underscores.

## Goals

1. Telegram actually parses markdown so `*bold*`, `_italic_`, `` `code` ``, code blocks, and links render.
2. Tool calls render as compact, humanized one-liners with consistent styling.
3. Each agent turn opens with a clean visual header so the user always knows which role is speaking.
4. Behavior change is invisible to agents — no prompt edits, no MCP changes.

## Non-goals

- The agent dashboard (separate spec).
- Per-role colors or per-role emoji prefixes — the chosen "editorial" style uses a single `▍` marker.
- Collapse/expand of long messages.
- Reactions, replies, threads, polls.
- Changing what AGENTS write (no system-prompt changes).

## Concept

A small **formatting layer** sits between the agent event stream and Telegram's send/edit calls. Everything new lives in one new file (`agent_hub/telegram_bot/formatting.py`) and one new constructor field on the streamer.

Each agent turn produces one logical message:

- **First bubble** opens with `▍ *<Display Name>*` + blank line.
- **Tool calls** render as italic lines prefixed with `›`: `_› Read store.py_`, `_› Bash · npm test --silent_`.
- **Prose** is treated as MarkdownV2 source: `**bold**` is translated to `*bold*`, reserved characters in unstyled segments are backslash-escaped.
- **Continuation bubbles** (when text exceeds Telegram's 4096-char limit) get no header — they flow as untitled bubbles so the turn reads as one long thought.

Streaming behavior stays as-is: text appears live, edits throttled to ~1.5s, tool-call lines appear inline as tools fire.

## Visual reference

The rendered first bubble of a reviewer turn:

> **▍ Reviewer**
>
> Good — two commits to verify, four changed files. Let me check each fix is correct rather than just present.
> *› Read DigestFeatured.tsx*
> *› Read ensureImageInR2.test.ts*
> *› Bash · git diff HEAD~2*
>
> All three fixes verified precisely:
>
> **Blocker 1.** `DigestFeatured.tsx:18` reads `s.isSlowEffective()`. ✓
> **Blocker 2.** Two-call R2-hit-skip test is correct. ✓
> **Concern 3.** `clearTimeout` in `finally`. ✓

## Tool-call humanization

`humanize_tool(tool_name: str, args: dict) -> str` produces the right-hand side of the `›` line.

### Built-in tools

| Raw | Rendered |
|---|---|
| `Read` | `Read README.md` (basename only) |
| `Edit` | `Edit streamer.py` |
| `Write` | `Write capture.py` |
| `Bash` | `Bash · <cmd>` (first 60 chars + `…` if longer) |
| `Grep` | `Grep "useState"` |
| `Glob` | `Glob "**/*.tsx"` |
| `WebSearch` | `Search · <query>` |
| `WebFetch` | `Fetch · <url>` |
| Unrecognized | The raw tool name unchanged |

### MCP tools (`mcp__agent_hub__*`)

| Raw | Rendered |
|---|---|
| `mcp__agent_hub__tasks_get` | `Read task #<id>` |
| `mcp__agent_hub__tasks_create` | `Create task "<title>"` |
| `mcp__agent_hub__tasks_update` | `Update task #<id> → <status>` (status only if present) |
| `mcp__agent_hub__tasks_comment` | `Comment on task #<id>` |
| `mcp__agent_hub__tasks_list` | `List tasks` |
| `mcp__agent_hub__handoff` | `Hand off to <to_agent>` |
| `mcp__agent_hub__gate_request` | `Request <kind> gate` |
| `mcp__agent_hub__worktree_path` | `Read worktree path` |
| `mcp__agent_hub__memory_note` | `Record project fact` |
| Other `mcp__agent_hub__*` | strip prefix, replace `_` with `.`, lowercase |

### Common rules

- **Path arguments** get basename-only treatment (`Read C:\dev\…\store.py` → `Read store.py`).
- **Truncation** at 60 chars for the detail portion of any tool, with trailing `…`.
- **Failed tools** get a follow-up line `_› failed_` on the next line (simpler than splicing into the already-streamed `ToolStart` line).

## MarkdownV2 escaping

Agent prose enters the formatter as a string with mixed intent: some segments are deliberately formatted (`` `code` ``, `*bold*`), most is plain prose. Telegram MarkdownV2 requires backslash-escaping of `_ * [ ] ( ) ~ > # + - = | { } . !` outside formatted segments.

`to_markdownv2(prose: str) -> str` does this in three passes:

1. **Tokenize.** Walk the string, splitting it into a list of `(kind, text)` tuples where `kind` is `"plain"`, `"bold"`, `"italic"`, `"code"`, `"codeblock"`, `"spoiler"`, or `"link"`. Matching markers are paired greedily; unmatched markers leave their text in `"plain"`.
2. **Normalize.** `**bold**` → `*bold*` and `__italic__` → `_italic_` so the agents' GitHub-style markdown works. The single-char variants are already MarkdownV2-native.
3. **Reassemble.** Plain segments get every reserved char backslash-escaped. Formatted segments are emitted with their MarkdownV2 markers; reserved characters inside `code` / `codeblock` need only `` ` `` and `\` escaped (per the MarkdownV2 spec).

If the resulting string would still fail Telegram's parser (rare, but possible for adversarial input), the `_safe_edit` retry path falls back to `parse_mode=None`.

### Other formatter helpers

- `role_header(display_name: str) -> str` returns `"▍ *<escaped name>*\n\n"`.
- `escape(text: str) -> str` is a thin wrapper around `telegram.helpers.escape_markdown(text, version=2)`. Used for tool-call lines and headers.

## Streaming integration

### `agent_hub/telegram_bot/streamer.py`

`StreamingMessage` gains:

- A `parse_mode: str | None = "MarkdownV2"` constructor field.
- `_send_initial` and `_safe_edit` pass `parse_mode=self.parse_mode` to `send_message` / `edit_text`.
- `_safe_edit` gains one extra `except BadRequest` branch that matches `/can't parse entities|can't find end/i` and retries once with `parse_mode=None` so a malformed escape never blocks the stream.
- The existing continuation behavior (when text > 4096 chars, send a new bubble) is unchanged. Callers pass the header in `prefix`, which already lands only in the first bubble.

### `agent_hub/telegram_bot/bot.py`

`_render_event` changes:

- The text branch: `await stream.append(to_markdownv2(event.text))`.
- The tool-start branch: replace `_summarize_tool` with `humanize_tool`; wrap result in italic markers: `_› {escape(line)}_\n`.
- The tool-end branch (error): append `_› failed_\n`.
- The error branch (`AgentError`): escape and bold the message: `*⚠ {escape(event.message)}*\n`.
- The existing `_summarize_tool` function is deleted.

The streaming initialization line builds the role header via `role_header(display_name)` instead of `f"*{display_name}*\n"`.

## File plan

**New:**
- `agent_hub/telegram_bot/formatting.py` — `humanize_tool`, `to_markdownv2`, `role_header`, `escape`, plus the tool-name table as a module-level dict
- `tests/test_formatting.py` — ~30 pure-function tests
- `tests/test_streamer_parse_mode.py` — 3 tests against a stub Bot

**Modified:**
- `agent_hub/telegram_bot/streamer.py` — `parse_mode` field, parse-error fallback (~15 lines)
- `agent_hub/telegram_bot/bot.py` — `_render_event` routing, header build, drop `_summarize_tool` (~20 lines)
- `tests/test_surface_telegram.py` — one end-to-end render test

## Testing

All tests are pure-function unit tests against fixture strings — no Telegram API, no mocking heavy dependencies.

- **`humanize_tool`** is covered by ~20 cases: each built-in, each MCP tool, the basename rule, Bash truncation, unknown-tool fallback.
- **`to_markdownv2`** is covered by ~10 cases: reserved-char escaping, preservation of inline code / fenced code / italic / bold / spoiler / link, translation of GitHub `**bold**` and `__italic__`, fallback when markers are unbalanced.
- **`role_header`** and **`escape`** get a few sanity cases each.
- **Streamer integration** verifies the `parse_mode="MarkdownV2"` kwarg reaches `send_message` / `edit_text`, that a `BadRequest("can't parse entities")` triggers one retry with `parse_mode=None`, and that continuation bubbles open without the role header.
- **End-to-end** test in `test_surface_telegram.py` runs a synthetic event sequence (text + tool start + tool end + text) through the renderer and asserts the final accumulated MarkdownV2 string matches a fixture.

No smoke test is required — the visual outcome is validated by eye after merge. Unit tests guarantee the formatter is correct.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Agent emits adversarial markdown that breaks MarkdownV2 parsing | `_safe_edit` falls back to `parse_mode=None` on `BadRequest` |
| Translation `**bold**` → `*bold*` interferes with existing single-`*` usage | `to_markdownv2` tokenizes greedily; tests cover both styles side-by-side |
| Tool name table goes stale when new MCP tools are added | Unknown tools fall back to a humanized form (`strip prefix → dot-name`) rather than raw `mcp__agent_hub__*` |
| Path basename is wrong on Windows-style paths | `pathlib.PurePath` handles both separators; covered by a test fixture using a Windows path |
| 60-char Bash truncation hides important info | Acceptable for MVP; user can run the same command in their own terminal to see the full thing |

## Out of scope for MVP

- The agent dashboard (separate cycle).
- Per-role emoji or colors.
- Collapse/expand or threaded replies.
- Reaction-based UX.
- Localizing tool labels (English only for now).
