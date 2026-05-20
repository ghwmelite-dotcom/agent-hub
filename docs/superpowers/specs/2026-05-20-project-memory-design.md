# Project Memory for Agent Hub

**Status:** Design
**Date:** 2026-05-20
**Author:** brainstorming session

## Problem

Agents in agent-hub have per-(agent, task_id) session continuity via the Claude Agent SDK, but no memory that persists across tasks or projects. Every new task starts cold:

- The architect re-discovers the codebase on each task.
- The reviewer flags the same kinds of issues repeatedly without those becoming durable rules.
- User corrections in Telegram ("don't add comments", "always use prepared statements") are forgotten as soon as the task ends.
- Design decisions made on task #38 are invisible by task #41.

The result is wasted tokens, drift between agents, and no compounding learning. The team gets no smarter over time.

## Goals

1. Give agents working in the same workspace a shared memory pool that survives task and process restarts.
2. Capture memory automatically at orchestrator events — agents don't have to remember to write.
3. Inject relevant memory into each agent's system prompt at task start.
4. Provide simple Telegram controls to inspect and prune memory.

## Non-goals

- LLM-based relevance ranking (recency + use_count are enough to start).
- Vector embeddings or semantic search.
- Cross-workspace memory sharing — strict per-workspace scope.
- Memory export/import.
- A web UI for memory management.

## Concept

A project-scoped memory store backed by the existing SQLite DB. Memory is **shared across all agents** working in a given workspace (keyed by absolute workspace path), **auto-captured** by the orchestrator at five hook points, and **injected into agent system prompts** at task start via a `Project memory` section.

Four memory types:

| Type | Captured when |
|---|---|
| `project_fact` | Agent calls `memory.note` MCP tool with a non-obvious convention (build cmd, stack, file layout). |
| `lesson` | Reviewer rejects, QA fails, or user `/reject`s a design — failure + reason becomes a lesson. |
| `preference` | User Telegram messages with corrective intent ("don't", "always", "from now on", "prefer", "stop", "never"). |
| `decision` | A design gate is `/approve`d — the architect/quant's design post becomes a decision-log entry. |

## Data model

New SQLite table `project_memory`, added via a schema migration in `agent_hub/db.py`:

```sql
CREATE TABLE project_memory (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace     TEXT    NOT NULL,            -- normalized absolute path
  type          TEXT    NOT NULL,            -- 'project_fact'|'lesson'|'preference'|'decision'
  agent_source  TEXT,                        -- role that wrote it ('reviewer', 'pm', 'user', ...)
  title         TEXT    NOT NULL,            -- short headline, ~80 chars
  body          TEXT    NOT NULL,            -- content
  related_task  INTEGER,                     -- task_id if captured from a task event
  created_at    TEXT    NOT NULL,
  last_used_at  TEXT,                        -- bumped each time included in a prompt
  use_count     INTEGER NOT NULL DEFAULT 0,
  archived      INTEGER NOT NULL DEFAULT 0   -- soft delete
);
CREATE INDEX idx_pm_workspace_type ON project_memory(workspace, type, archived);
CREATE INDEX idx_pm_last_used      ON project_memory(workspace, last_used_at);
```

**Field rationale:**

- `workspace` matches the existing `/workspace` allowlist semantics.
- `agent_source` enables per-role bias (a `pm` preference about being terse shouldn't necessarily go into fullstack's prompt) and post-hoc analysis ("what does reviewer keep flagging?").
- `use_count` + `last_used_at` drive decay.
- `archived` instead of hard delete — `/forget` is reversible.

**Decay rules (MVP):**

- `lesson` keeps its slot in auto-injection if `use_count > 0` OR `created_at` within 30 days. Older unused lessons surface only via explicit search.
- `project_fact`, `preference`, `decision` don't decay — they're durable.

## Write path — auto-capture

All capture is orchestrator-driven; agents don't write directly (except via `memory.note` for project facts).

`agent_hub/memory/capture.py` exposes five hook functions:

| Hook | Fires from | Writes |
|---|---|---|
| `on_reject(task_id, reason)` | telegram_bot `/reject` handler | `lesson` — title from task title, body = user's reason, `agent_source='user'`, `related_task=task_id` |
| `on_reviewer_kickback(task_id, comment)` | orchestrator handoff routing when reviewer → fullstack | `lesson` — body = reviewer's comment, `agent_source='reviewer'` |
| `on_qa_fail(task_id, agent_name, details)` | orchestrator handoff routing on QA/backtest-analyst failure | `lesson` — body = failure details, `agent_source=agent_name` |
| `on_design_approved(task_id, design_text, agent_name)` | `gate.approve` handler | `decision` — title = task title, body = design post, `agent_source=agent_name` (`architect` or `quant`) |
| `on_user_preference_candidate(message_text)` | telegram_bot message handler when regex matches | Sends inline-keyboard prompt to user (`Save` / `Skip`). On `Save`, writes `preference` — body = message, `agent_source='user'`. |

**Preference detection** is a deterministic regex on incoming Telegram messages (no LLM classification):

```python
PREFERENCE_MARKERS = re.compile(
    r"\b(don'?t|stop|never|always|from now on|prefer|please don'?t)\b",
    re.IGNORECASE,
)
```

On match, the message gets an inline-keyboard prompt: *"Save as project preference? [Save] [Skip]"*. This prevents memory pollution from casual instructions while keeping the capture path deterministic and cheap.

**Project facts** are the one type without an obvious orchestrator trigger. They're captured via an explicit MCP tool — `memory.note(type='project_fact', title, body)` — that any agent can call when it discovers a non-obvious convention.

**Deduplication on insert:** before writing, look for non-archived rows of the same `workspace` + `type` with an identical `title`. On exact-title match, skip insert and bump `use_count` instead. Prevents repeated identical lessons from the reviewer flagging the same thing across many tasks.

## Read path — system prompt injection

`agent_hub/agents/runner_options.py` gains a step after composing the base system prompt from the role YAML: it appends a `## Project memory` section built by `MemoryStore.load_for_prompt(workspace, agent_name)`.

**Assembled section example:**

```
## Project memory — C:\dev\your-project

### Conventions
- [Build] use `npm run build:prod` not `npm build`   (used 12×)
- [Stack] Workers + D1, no Postgres                  (used 8×)

### Preferences (from user)
- Don't add code comments unless asked
- Prefer one bundled PR over many small ones for refactors

### Recent lessons
- Reviewer keeps flagging unawaited promises in handlers — always `await`
- QA failed task #34: missing `prepared` statement in user.create

### Recent decisions
- Task #41: chose Drizzle over raw SQL — reason: type safety with D1
- Task #38: rejected websocket approach, went with SSE
```

**Selection rules** (per agent, per task spawn):

- `project_fact` — top 10 by `use_count`, then recency.
- `preference` — all non-archived.
- `lesson` — last 5 by recency, filtered by relevance to the role (see per-role filtering below).
- `decision` — last 5 by recency.

**Per-role filtering** (mapping in `runner_options.py`):

| Role | Sees |
|---|---|
| `pm`, `architect`, `quant` | All four types |
| `fullstack`, `implementer` | facts, preferences, lessons |
| `reviewer` | All four (needs full picture to judge) |
| `qa`, `backtest-analyst` | facts, lessons |
| `researcher`, `senior-uiux-designer` | facts, preferences |

**Size cap:** assembled memory section is hard-capped at ~2000 tokens (estimated by `len(text) / 4`). If over: drop oldest lessons first, then oldest decisions. `project_fact` and `preference` entries are never dropped — if they alone exceed the cap, log a warning. That's a signal the user should prune via `/memory`.

**Bookkeeping:** every entry included in a prompt has its `last_used_at` bumped and `use_count` incremented, in one bulk UPDATE at the end of `load_for_prompt`.

**Session-resume interaction:** the existing `AgentSessionStore` reuses the same SDK session UUID across restarts, which means the CLI has the original system prompt cached. To force memory refresh when memory has materially changed, the runner computes a `memory_fingerprint` (SHA-256 of the assembled memory section) and compares it against the last fingerprint stored for that (agent, task_id). On mismatch, call `session_store.forget(agent, task_id)` so the next connect builds a fresh system prompt. This is the only structural change to the existing session machinery.

A new column `memory_fingerprint TEXT` is added to the existing `agent_sessions` table to hold the last-known fingerprint.

## User surface

### Telegram commands (added to `agent_hub/telegram_bot/`)

| Command | Behavior |
|---|---|
| `/memory` | List memory for current workspace, grouped by type. Each line shows id, title, type, age, use_count. Paginated if >20. |
| `/memory <type>` | Filter to one type — `facts`, `lessons`, `preferences`, `decisions`. |
| `/forget <id>` | Soft-delete (archived=1). One-line preview before confirmation. |
| `/memory clear` | Wipe memory for **current workspace only**. Requires `/memory clear confirm` to actually execute. |
| `/remember <text>` | Manual add as `preference` (escape hatch when the regex misses an intent). |

The preference-candidate prompt from the write path uses inline-keyboard `Save` / `Skip` buttons.

### MCP tools (added to `agent_hub/mcp_server/tools/memory.py`)

| Tool | Used by | Behavior |
|---|---|---|
| `memory.note(type, title, body)` | Any agent | Insert a memory entry. `type` restricted to `project_fact` for MVP. Other types only via auto-capture. Sets `agent_source` from the calling agent. |
| `memory.search(query, type?, limit?)` | Any agent | Optional drill-down. SQL `LIKE` on title+body, scoped to workspace. Returns up to 10. Bumps `use_count` on hits. **Optional for MVP** — ship without it unless prompt injection proves insufficient. |

No `memory.forget` MCP tool. Deletion is a user-only operation via Telegram.

## File plan

**New files:**

- `agent_hub/memory/__init__.py`
- `agent_hub/memory/store.py` — `MemoryStore` class: CRUD, `load_for_prompt`, fingerprint, dedupe-on-insert, decay query
- `agent_hub/memory/capture.py` — five capture functions
- `agent_hub/mcp_server/tools/memory.py` — `memory.note` (+ `memory.search` if in scope)
- `tests/unit/memory/test_store.py`
- `tests/unit/memory/test_capture.py`
- `tests/unit/memory/test_runner_options.py`

**Modified files:**

- `agent_hub/db.py` — add migration: `project_memory` table + indexes, add `memory_fingerprint` column to `agent_sessions`
- `agent_hub/agents/runner_options.py` — call `load_for_prompt`, append section, compare fingerprint, call `session_store.forget` on mismatch
- `agent_hub/agents/session_store.py` — read/write `memory_fingerprint`
- `agent_hub/orchestrator/*` — wire capture hooks into reject, reviewer-kickback, qa-fail, design-approve paths
- `agent_hub/telegram_bot/*` — add `/memory`, `/forget`, `/remember`, `/memory clear`; preference-candidate inline keyboard
- `tests/smoke/` — extend smoke test to assert one `decision` is written end-to-end (the happy path produces a `/approve`, not a kickback; lesson capture is exercised in unit tests with synthetic events)

## Testing

Mirrors existing repo patterns:

- Unit tests run against a temp SQLite via `pytest` fixtures, no mocking of the DB.
- Capture tests fire synthetic events into hook functions and assert on rows in `project_memory`.
- `runner_options` tests assert the assembled system prompt string contains/excludes expected sections — pure function, no SDK calls.
- Fingerprint-change → `session_store.forget` is verified at the runner level.
- Smoke test stays a real end-to-end run; adds a single assertion that a `decision` row was written for the approved design.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Memory pollution from every casual user message | Preference candidate prompt requires explicit `Save` button — no silent capture. |
| Stale lessons crowd the prompt | Decay rule: unused lessons older than 30 days fall out of auto-injection. |
| Prompt bloat as project grows | Hard 2000-token cap; user has `/memory` and `/forget` controls; `use_count` lets stale facts age out. |
| Per-role memory leak (e.g., PM's preferences poisoning fullstack) | Per-role filtering table in `runner_options.py`; covered by unit tests. |
| Memory and cached SDK session disagree on system prompt | Fingerprint compare → `session_store.forget` on mismatch forces a clean re-attach. |
| Duplicate lessons from repeated review failures | Title-based dedupe on insert; `use_count` bump instead of new row. |

## Out of scope for MVP

- LLM-based relevance ranking.
- Vector embeddings / semantic search.
- Cross-workspace memory sharing.
- Memory export/import.
- Web UI for memory management.
- `memory.search` MCP tool — optional, ship without unless prompt injection proves insufficient.
