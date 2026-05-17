# Coordinated agent team — design spec

**Date:** 2026-05-17
**Status:** Approved (pending implementation plan)
**Scope:** Evolve agent_hub from a router-to-individual-agents into a coordinated team that ships PRs autonomously, with two human gates and parallel task execution.

## 1. Goals & non-goals

### Goals
1. Agents hand work to each other directly — no human in the routing loop between tasks.
2. The PM agent intakes any-sized user request and decomposes it into a flat task or an epic with sub-tasks.
3. The team operates on one project (e.g. baobab) but runs multiple tasks in parallel via git worktrees.
4. Two human gates: the architect's design, and the final pushed branch. Everything in between runs unattended.
5. "Ship" means: `git push origin task/<id>-<slug>`. No PR is opened by the team. Branch lives on GitHub for human review and manual PR creation.

### Non-goals
- Multi-project parallel work (one workspace per agent_hub process; switching is manual).
- High-availability / multi-process agent_hub (single-process; lock file enforces).
- Auto-merge to main or auto-PR creation.
- Rollback of pushed branches (manual).
- Cross-workspace tasks (a task touching two repos).

## 2. Constraints (user-confirmed)

| Constraint | Value |
|---|---|
| Human checkpoints | Architect's design + the final pushed branch |
| Task size | PM decides per task (flat task ↔ epic with sub-tasks) |
| Project scope | One project, multiple concurrent tasks via worktrees |
| Ship action | Commit + push branch to origin; no PR created |

## 3. Architecture

Three processes, sharing one SQLite database.

```
┌─────────────────────────────────────────────────┐
│  agent_hub (host process)                       │
│  ─ Telegram bot                                 │
│  ─ Orchestrator (gate coordinator + dispatcher) │
│  ─ AgentRunner (spawns ClaudeSDKClient/agent)   │
└────┬────────────────────────────────────────────┘
     │ spawns N ClaudeSDKClients (one per (agent, task))
     ▼
┌─────────────────────────────────────────────────┐
│  ClaudeSDKClient (per-agent subprocess)         │
│  ─ runs the agent's turn                        │
│  ─ launches its own MCP child via stdio         │
└────┬────────────────────────────────────────────┘
     │ each SDK client launches its own MCP client
     ▼
┌─────────────────────────────────────────────────┐
│  agent-hub-mcp (per-agent stdio MCP server)     │
│  ─ tools: tasks.*, handoff, worktree.*, gate.*  │
│  ─ reads/writes shared SQLite (WAL mode)        │
└─────────────────────────────────────────────────┘
                          │
                          ▼
              ┌────────────────────────┐
              │  data/agent_hub.db     │
              │  (SQLite, WAL mode)    │
              │  tasks, events,        │
              │  handoff_queue,        │
              │  gates, worktrees      │
              └────────────────────────┘
```

### Communication model
- **Agent → MCP**: in-process stdio, structured tool calls. The only way agents act on shared state.
- **MCP → orchestrator**: indirect, via SQLite. Orchestrator polls a `handoff_queue` table every ~250ms; when a row appears, claims it (atomic UPDATE) and dispatches to the named agent via the runner.
- **Orchestrator → Telegram**: existing python-telegram-bot path. New commands: `/tasks`, `/task <id>`, `/approve <id>`, `/reject <id> <reason>`, `/budget <id> <amount>`, `/resume <id>`.

### Key architectural choices

- **SQLite + polling for the MCP-to-orchestrator channel.** The MCP servers are grand-children of agent_hub (subprocess of SDK client, which is subprocess of agent_hub). Two-hop IPC is awkward. A shared DB with WAL gives concurrent multi-writer safety, survives process restarts, and 250ms polling is invisible at this scale.
- **Per-agent MCP processes**, not a shared server. The SDK's MCP support naturally launches one MCP child per `ClaudeSDKClient`. Sharing one server across SDK clients would require a network transport (HTTP/SSE) and connection pooling — extra complexity for no real gain.
- **One repo, N worktrees.** When a task moves to `ready`, the orchestrator creates `worktrees/<task-id>/` as a git worktree on branch `task/<id>-<slug>` from `main`. The runner sets the agent's `cwd` to that worktree for any turn on that task. Cleanup happens after `done` + push.

## 4. Components

### 4.1 Data layer — schema additions (extends `agent_hub/db.py`)

Five new tables. Existing `messages` table unchanged.

```sql
CREATE TABLE tasks (
  id INTEGER PRIMARY KEY,
  parent_id INTEGER REFERENCES tasks(id),  -- epic→leaf tree
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  status TEXT NOT NULL,
  owner TEXT,                              -- canonical agent name
  worktree_path TEXT,
  branch_name TEXT,
  origin_chat_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE task_events (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,            -- agent name or "user" or "orchestrator"
  kind TEXT NOT NULL,             -- comment|status_change|handoff|gate_request|gate_resolve|push|error
  payload_json TEXT NOT NULL
);

CREATE TABLE handoff_queue (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  from_agent TEXT NOT NULL,
  to_agent TEXT NOT NULL,
  message TEXT NOT NULL,
  enqueued_at TEXT NOT NULL,
  claimed_at TEXT                  -- NULL = available; atomic claim sets this
);

CREATE TABLE gates (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  kind TEXT NOT NULL,              -- design (only kind in v1; push happens automatically once QA passes)
  artifact_path TEXT,
  summary TEXT,
  requested_at TEXT NOT NULL,
  resolved_at TEXT,
  resolution TEXT                  -- approved | rejected | NULL while pending
);

CREATE TABLE worktrees (
  task_id INTEGER PRIMARY KEY REFERENCES tasks(id),
  path TEXT NOT NULL,
  branch TEXT NOT NULL,
  base_branch TEXT NOT NULL,
  created_at TEXT NOT NULL,
  cleaned_at TEXT
);
```

### 4.2 Task status state machine

```
pending → planning → design_review → ready → in_progress → review → done
              ↑           │                       ↑          │
              └───────────┘                       └──────────┘
              (on /reject)                  (on reviewer kick-back)

blocked: parallel marker reachable from any state; resolved via /resume → planning
```

**Explicit transition table** — each transition is allowed; everything else returns a tool error.

| From | To | Trigger |
|---|---|---|
| (none) | `pending` | `tasks.create` |
| `pending` | `planning` | PM, on intake |
| `planning` | `design_review` | architect, after producing design |
| `planning` | `in_progress` | PM, for small tasks that skip the architect |
| `design_review` | `ready` | orchestrator, on `/approve <id>` |
| `design_review` | `planning` | orchestrator, on `/reject <id> <reason>` |
| `ready` | `in_progress` | fullstack, on first turn |
| `in_progress` | `review` | fullstack, on handoff to reviewer |
| `review` | `done` | QA, after pass |
| `review` | `in_progress` | fullstack, picks back up on reviewer kick-back |
| *(any)* | `blocked` | orchestrator (spend cap, stuck loop, gate timeout, error retry exhausted) |
| `blocked` | `planning` | orchestrator, on `/resume <id>` — hands to PM with block context |

Transitions are validated in Python (not by DB CHECK) for cheaper migration of new states later. The allowed-transition map is data, tested exhaustively (Tier 1 `test_transitions.py`).

**Rejection**: when a design is rejected, the `gates` row records the rejection (`resolution='rejected'` + the user's reason in `summary`); the task status moves directly back to `planning`. There is no separate `rejected` status.

**Epic auto-completion**: when the last leaf of an epic (a task with `parent_id`) transitions to `done`, the orchestrator marks the parent epic `done` and DMs the user with all leaf branch URLs.

### 4.3 MCP server (`agent_hub/mcp_server/`, new package)

Python stdio MCP server using the `mcp` SDK already in our deps. ~400 lines. Stateless beyond the DB. Exposes these tools:

```python
# Task lifecycle
tasks.create(title, description, parent_id=None, owner=None) -> task_id
tasks.get(task_id) -> {task, recent_events: 20}
tasks.list(status=None, owner=None, parent_id=None) -> [task_summary]
tasks.tree(task_id) -> {root, descendants}
tasks.update(task_id, status=None, owner=None, ...) -> task
tasks.comment(task_id, body) -> event_id

# Coordination — the verb that makes it a team
handoff(to_agent, task_id, message) -> {enqueued: True, queue_id}

# Workspace
worktree.create(task_id, base_branch="main") -> {path, branch}
worktree.path(task_id) -> str | None

# Human gates (kind='design' is the only kind in v1)
gate.request(task_id, kind, artifact_path=None, summary=None) -> gate_id
gate.status(task_id, kind) -> "pending"|"approved"|"rejected"|"none"
```

Launched per-agent by each `ClaudeSDKClient` via `ClaudeAgentOptions(mcp_servers=[{"command":"python","args":["-m","agent_hub.mcp_server"]}])`.

### 4.4 Orchestrator (extends `agent_hub/orchestrator/`)

Three new responsibilities on top of today's routing:

- **Handoff loop** (background asyncio task, 250ms tick): atomic claim from `handoff_queue`, routes the message to the named agent via `runner.send(...)`. Task context is prepended to the message body so the receiving agent has the picture:
  ```
  [task #N, from @X] {message}

  Task context: {tasks.get summary}
  ```
- **Gate watcher** (same loop): when a row appears in `gates` with `resolved_at IS NULL`, DM the user. Reminder at 24h; auto-block at 7 days; never auto-approve.
- **Task-aware Telegram interpretation**: `/approve <id>` resolves a pending gate; messages in a task thread without a slash command become `tasks.comment` + handoff to the current owner. Free-form text without a sticky task routes to PM as today.

### 4.5 AgentRunner updates (extends `agent_hub/agents/runner.py`)

Two changes:
- `send(agent_name, message, task_id=None)`. When `task_id` is given, look up `worktrees.path` and start (or re-create) the SDK client with `cwd=that_path`.
- **Per-(agent, task) client pool**, not just per-agent. The `_clients` dict is keyed on `(agent_name, task_id)`. The implementer working on task #5 and task #7 has two separate sessions.
- `ClaudeAgentOptions(mcp_servers=[agent_hub_mcp])` added at client construction. Each role YAML's `allowed_tools` gains `mcp__agent_hub__*` (or specific tool names if we want per-role scoping — e.g. PM gets `tasks.update`, reviewer doesn't).

### 4.6 Worktree manager (`agent_hub/worktrees.py`, new ~120 lines)

Wraps `git worktree add` / `git worktree remove`. Computes branch names from titles (slug, deduped on collision: `task/42-add-health`, `task/42-add-health-v2`). Stores worktree roots under `<workspace>/../worktrees/<id>/` to keep the main repo clean. **Refuses to clean a worktree with uncommitted changes** (safety).

### 4.7 Agent role updates (existing YAMLs, prompt-only changes)

- **PM**: new section teaching the intake → decompose → assign loop. Every user request starts with `tasks.create`. Sizing heuristic: 1 file + <30min = direct fullstack handoff; multi-file feature = architect first; >1 day = decompose into sub-tasks.
- **Architect**: must end every design session with `tasks.comment` (the design) + `gate.request(task_id, kind='design')` + `tasks.update(status='design_review')` and stop.
- **Fullstack-engineer / Implementer**: before first edit, call `worktree.path(task_id)` to confirm cwd. After last edit, `handoff('reviewer', task_id, ...)`.
- **Reviewer**: if approved, `handoff('qa', ...)`. If blockers, `handoff('fullstack-engineer', ..., feedback)`.
- **QA**: if pass, `tasks.update(status='done')` (triggers orchestrator's push action). If fail, `handoff('fullstack-engineer', ..., failures)`.

### 4.8 Telegram surface (extends `agent_hub/telegram_bot/`)

Three new command handlers; existing free-form routing to PM unchanged.

- `/tasks` — list active tasks grouped by status
- `/task <id>` — task detail + recent 20 events
- `/approve <id>` / `/reject <id> <reason>` — resolve a pending gate
- `/budget <id> <amount>` — raise the per-task spend cap after a block
- `/resume <id>` — resume a task. For tasks paused at restart (still in their prior status), re-dispatches to the current owner. For `blocked` tasks (cap/stuck/timeout/error), hands off to PM with the block context so PM can decide the next action.

## 5. Data flow

### Flow A — Single feature with design gate (canonical case)

```
USER → @pm: "Add a /health endpoint that pings D1"

[PM turn]
  tasks.create(title="add /health endpoint")  → #42
  tasks.update(42, status="planning", owner="pm")
  (sizes: multi-file, medium → architect first)
  handoff("architect", 42, "Design a /health endpoint that pings D1...")
  replies: "Filed as task #42, architect is taking the design pass."

[Orchestrator handoff loop: claim → runner.send("architect", ..., task_id=42)]
[Architect turn, runs in main checkout (read-only work fine)]
  tasks.get(42)
  (reads existing routes, schema)
  produces design
  tasks.comment(42, body=<design>)
  gate.request(42, kind="design")
  tasks.update(42, status="design_review")

[Orchestrator gate watcher: pending design on #42 → DM user]
BOT → user: "🛂 Task #42 design ready. Reply /approve 42 or send feedback."

USER → "/approve 42"

[Orchestrator: resolve(approved) → status=ready → worktree.create(42) → handoff("fullstack-engineer", 42)]
[Fullstack turn, cwd = worktrees/42/, branch task/42-add-health-endpoint]
  tasks.get(42)  (reads design)
  worktree.path(42)  (confirms cwd)
  writes route, registers in worker/src/index.ts, typecheck, commit
  handoff("reviewer", 42, "Implemented, typecheck green.")

[Reviewer turn]
  tasks.get(42), git diff main..HEAD
  tasks.comment(42, "LGTM. One nit logged.")
  handoff("qa", 42, "Approved, nit non-blocking.")

[QA turn]
  runs tests
  tasks.update(42, status="done")

[Orchestrator: status=done → git push origin task/42-add-health-endpoint]
BOT → user: "✅ Task #42 done. Pushed: <github-url>"
```

### Flow B — Epic with 3 sub-tasks running in parallel

```
USER → @pm: "Add Stripe billing — pricing, checkout, webhook, portal"

[PM turn]
  tasks.create(title="Stripe billing")  → #43 (epic)
  tasks.create(parent_id=43, "Pricing page")              → #44
  tasks.create(parent_id=43, "Checkout + webhook")        → #45
  tasks.create(parent_id=43, "Customer portal entry")     → #46
  handoff("architect", 43, "Top-level architecture for billing")

[Architect → gate.request(43)]
USER → /approve 43

[Orchestrator: cascade ready to leaves → parallel worktree.create for #44, #45, #46
  → parallel handoffs to "fullstack-engineer" #44, "fullstack-engineer" #45, "ui-ux" #46]

[3 concurrent agent turns in 3 worktrees]
  Fullstack(#44)    Fullstack(#45)    UI/UX(#46)
       ↓                 ↓                 ↓
   review→qa→done    review→qa→done    review→qa→done

[Orchestrator: when all leaves done, mark epic #43 done, DM 3 branch URLs]
```

**Concurrency property:** the per-(agent, task) client pool is what makes this work. SQLite WAL handles parallel MCP writes. The orchestrator's handoff loop processes the queue serially but dispatches asyncio tasks that run in parallel.

### Flow C — Design rejected with feedback

```
[Start same as Flow A through gate.request]
BOT → user: "🛂 Task #42 design ready..."

USER → "Reject — D1 ping should be SELECT 1, not real query. Add 5s timeout."

[Orchestrator: message not a /command in task thread with pending gate →
   tasks.comment(42, body=<user msg>)
   gate.resolve(rejected)
   tasks.update(42, status="planning")
   handoff("architect", 42, "User rejected with feedback: ...")]

[Architect revises → tasks.comment + gate.request again]
[Loop until approval]
```

**Surface mode rule**: the Telegram bot distinguishes free-form-to-PM from message-in-task-context based on whether the chat has a pending gate awaiting reply.

## 6. Error handling

Principle: **stop and surface, don't auto-recover beyond one retry.** The system is more useful pausing loudly than papering over real problems.

### A. Agent-level failures
- **SDK exception during turn**: `AgentError` event → mark task `blocked`, log event, retry handoff once after 30s. Still failing → DM user.
- **Malformed tool call**: MCP validates with pydantic, returns structured error to agent. Agent self-corrects on next turn.
- **Agent ends turn without terminal action** (no handoff / status update / gate request): orchestrator sends a follow-up: *"Your turn ended without a handoff, status update, or gate request. What's next?"* — one nudge, then `blocked`.

### B. State & concurrency
- **Invalid state transitions**: `tasks.update` consults allowed-transitions map; returns tool error on disallowed.
- **Concurrent updates**: SQLite WAL + `BEGIN IMMEDIATE` for multi-step writes. Status updates use the transition validator (conflict explicit).
- **Handoff queue race**: atomic claim via `UPDATE ... WHERE id=? AND claimed_at IS NULL RETURNING *`.
- **Single orchestrator**: lock file at `data/.orchestrator.lock` with PID prevents two agent_hub processes.

### C. Git / filesystem
- **Branch collision**: worktree manager appends `-v2`, `-v3`; warns in `task_events`.
- **Push rejected**: orchestrator catches exit code, marks `blocked`, DMs user with exact stderr. **No retry.**
- **Dirty worktree at cleanup**: refuse delete. Surface as `blocked`.
- **Worktree path missing** (user deleted manually): runner catches FileNotFoundError, recreates from recorded branch; if recreation fails, blocks.

### D. Process lifecycle
- **Restart with in-flight tasks**: on boot, scan `tasks WHERE status IN (in_progress, review, planning)`. For each with last event >5min ago, DM user with the list and ask `/resume <id>`. **No auto-resume.**
- **MCP child dies**: agent's tool call fails → AgentError → category A.
- **Orchestrator hang**: watchdog asyncio task pings the loop every 5s; if no tick for 30s, log critical and force-restart the loop coroutine (process stays up).
- **Orphan worktree detection**: on boot, log + list but **never auto-delete**. Manual cleanup only.

### E. Human failures (gate timeouts)
- **No response to pending gate**: 24h reminder DM; 7-day auto-block; **never auto-approve**.
- **Ambiguous reply** ("looks fine I guess"): orchestrator treats as comment, asks for explicit `/approve <id>` or `/reject <id> <reason>`.
- **Wrong gate id** in /approve: validates existence + pending state; if not, replies with the actual pending-gate list.

### F. Budget & runaway control
- **Per-task cap** (`MAX_TASK_USD`, default $5): sum `TurnDone.cost_usd` per task across agents. At 80%, mark `blocked` + DM with option `/budget <id> <amount>` to raise. Hard stop at 100%.
- **Per-day system cap** (`MAX_DAILY_USD`, default $20): same mechanism system-wide. Resets at midnight UTC.
- **Stuck-loop detection**: 5+ consecutive turns from same agent on same task with no `handoff`, `gate.request`, or `tasks.update(status=...)` → interrupt, mark `blocked`, DM user with last few turn summaries.

### Explicitly not handled
- Multi-process agent_hub HA.
- Cross-workspace tasks.
- Auto-merge or auto-PR.
- Rollback of pushed branches.

## 7. Testing strategy

Three tiers. Principle: **most tests pure, fast, free of SDK calls.** Real LLM behavior is non-deterministic — the suite covers orchestration, not the model.

### Tier 1 — Pure unit tests (~70%)
Fast, in-memory or temp-file, zero network. Every commit.

- `tests/test_tasks.py` — create, get, list, tree. One test per allowed + disallowed transition (driven from transition map data).
- `tests/test_handoff_queue.py` — 10-thread race for the same row, exactly one wins.
- `tests/test_gates.py` — request → status pending; resolve(approved) → status approved; double-approve no-op; unknown gate errors.
- `tests/test_worktrees.py` — slug edge cases (emojis, spaces, long titles); refuses to delete dirty worktree. Temp git repos via `git init`.
- `tests/test_mcp_tools.py` — one test per tool: happy path + each input-validation failure. Tests call functions directly, no MCP server.
- `tests/test_router.py` — `/approve 42` resolves gate; `/approve abc` errors; free-form text routes to PM unless pending gate; sticky-task interpretation.
- `tests/test_transitions.py` — parameterized test over the entire transition map.
- `tests/test_spend.py` — synthetic `TurnDone` events feed accumulators; block fires at exactly 80% and 100%; daily resets at midnight.

### Tier 2 — Integration tests (~25%)
Real SQLite (temp file), real git, **fake agent runner**. Every PR. ~30s suite.

- **`FakeAgentRunner`** — subclasses `AgentRunner`, replays scripted event sequences per `(agent_name, task_id)`. Each scripted turn is a list of `(TextChunk | ToolStart | ToolEnd | TurnDone)` events.
- `tests/integration/test_flow_a.py` — Flow A end-to-end: scripted PM, architect, gate, fullstack, reviewer, QA. Assert branch exists, task is `done`, push command would have been issued.
- `tests/integration/test_flow_b.py` — Flow B parallel epics: assert 3 worktrees created, 3 client pool entries, no DB lock errors, all leaves done.
- `tests/integration/test_flow_c.py` — Flow C rejection loop: assert event log has both gate rows.
- `tests/integration/test_restart_resume.py` — write `in_progress` tasks, kill orchestrator, restart, assert user-surface DM is generated.
- `tests/integration/test_spend_cap.py` — scripted turns accumulate cost; assert orchestrator blocks next handoff at cap, sends DM.
- `tests/integration/test_stuck_loop.py` — script 5 consecutive no-action turns; assert `blocked`.
- `tests/integration/test_lock_file.py` — spawn second agent_hub against same `data/` dir; assert refusal.

### Tier 3 — End-to-end smoke (~5%, gated)
Real Claude SDK, cheap model, real git. **Skipped by default; runs only when `RUN_SMOKE_TESTS=1`.** Pre-release gate.

- Single Haiku-pinned smoke task: file a "Add a comment to README" task, wait for `done`, assert branch + commit. ~$0.05/run.
- One Telegram round-trip: documented manual checklist, not automated.

### Tooling
- `pytest` + `pytest-asyncio` (works with the existing `anyio` async stack)
- `pytest-xdist` for parallel runs (pure suite is embarrassingly parallel)
- Fixtures: `temp_db`, `temp_git_repo`, `fake_runner`, `clock` (freeze time for spend-window tests)
- `coverage.py` — informational only, no hard threshold

### What we explicitly don't test
- Real LLM behavior (smoke tier only; non-deterministic by nature).
- Telegram network layer (mocked at PTB application boundary).
- `git push` to real remote (mocked — we test we *would* push the right ref).
- MCP protocol wire format (`mcp` SDK tests its own transport).

## 8. Migration & rollback

- Schema changes are **purely additive** — no migration of existing `messages` table.
- Rollback path: switch the codebase back. Old code works against old + new tables (new tables are inert).
- **First-task post-deploy smoke** (manual runbook step): file a trivial task, watch it traverse the state machine. Lock file makes rollback safe if anything is wrong.

## 9. Open questions

None identified at design time. Any surface during implementation should be raised against this spec.

## 10. Implementation order (suggested for the plan phase)

Roughly in dependency order; the writing-plans skill will refine:

1. Data layer (tables, transition map, tests)
2. MCP server (tools, tests)
3. Runner: per-(agent, task) pool + cwd switching + MCP injection
4. Worktree manager
5. Orchestrator: handoff loop + gate watcher + lock file
6. Telegram command handlers
7. Agent role prompt updates
8. Spend cap + stuck-loop detection
9. Restart-resume + orphan detection
10. End-to-end smoke task on Haiku
