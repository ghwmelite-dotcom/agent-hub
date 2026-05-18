# Agent Hub

A team of specialized Claude agents you manage over Telegram. The agents run
locally on your machine using the Claude Agent SDK (the same engine that powers
Claude Code), so they have real filesystem, git, and shell access to whatever
project folder you point them at.

File a task in chat → architect designs → you `/approve` → fullstack-engineer
commits in an isolated git worktree → reviewer signs off → QA verifies → the
branch is pushed to your `origin`. The whole loop runs end-to-end without you
having to leave Telegram.

## The team

| Role                      | What they do                                                              |
| ------------------------- | ------------------------------------------------------------------------- |
| **Senior PM**             | Default chat partner. Files tasks, sizes work, picks the right design + QA agent. |
| **Architect**             | Designs implementation before code is written. Requests the design gate. (General engineering.) |
| **Quant Strategist**      | Designs trading logic — signals, risk math, regime filters — for EA / MetaTrader / MQL tasks. Requests the design gate. |
| **Fullstack Engineer**    | Implements features in the per-task git worktree. Loads MQL skills automatically on trading projects. (Opus 4.7 by default.) |
| **Implementer**           | Lighter-weight code work; alternative to fullstack for narrow changes.   |
| **Reviewer**              | Independent second pass on diffs, design choices, migrations.            |
| **Researcher**            | Web search, library evaluation, documentation reading.                   |
| **Senior UI/UX Designer** | Wireframes, design tokens, component decisions.                          |
| **QA**                    | Runs tests, validates against spec, marks the task done.                 |
| **Backtest Analyst**      | Validates statistical edge for EA tasks — equity curves, Monte Carlo, parameter robustness, overfit detection. Replaces QA on trading work. |

Roles live in `agent_hub/agents/roles/*.yaml` — edit the system prompts, models,
and tool allowlists without touching code.

### Two parallel chains

The team has two chains and PM picks which one runs per task.

**Standard chain** (web, CLI, API, library, mobile, infra):

```
PM → Architect → /approve → Fullstack Engineer → Reviewer → QA → push
```

**EA chain** (trading, EA, MetaTrader, MQL, backtests):

```
PM → Quant Strategist → /approve → Fullstack Engineer → Reviewer → Backtest Analyst → push
```

PM detects EA tasks from keywords in the user's message AND from
`Glob '**/*.mq{4,5}'` on the workspace, and prefixes the task title with
`[EA]` so the reviewer can route the QA step to backtest-analyst instead of qa.
You can force a chain manually by phrasing the task ("design an EA that…" vs
"build a worker that…") or by addressing the agent directly (`@quant …`).

## How the loop runs

1. You `@pm <ask>` — PM files a task and hands off to the architect.
2. Architect reads the codebase, posts a design as a task comment, requests the
   design gate (which moves the task to `design_review`).
3. You reply `/approve <task_id>` (or `/reject <task_id> <reason>`).
4. `/approve` creates a git worktree at `<repo_root>/../worktrees/<task_id>/` on
   branch `task/<id>-<slug>` and hands off to fullstack-engineer.
5. Fullstack commits in the worktree, hands off to reviewer.
6. Reviewer signs off (or kicks back to fullstack), then hands off to QA.
7. QA verifies and marks the task `done`.
8. Orchestrator pushes the branch to `origin` and DMs you the result.

Every handoff is persisted in SQLite, so the orchestrator survives restart
mid-task without losing position. The agent-side conversation history is
re-attached to the same SDK session UUID on reconnect — agents pick up where
they left off rather than starting fresh.

## Quick start

### 1. Create a Telegram bot

- Open Telegram, search for `@BotFather` (look for the blue check).
- Send `/newbot`, pick a display name and a username ending in `bot`.
- Save the token (looks like `7891234567:AAH-xxxxx...`).

### 2. Find your Telegram user ID

Message `@userinfobot` — it replies with your numeric ID. The bot refuses
messages from anyone else, so only you can drive it.

### 3. Install

```powershell
cd C:\dev\agent-hub
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Configure

```powershell
copy .env.example .env
notepad .env
```

Required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`.

### Auth: subscription vs API key

By default `ANTHROPIC_AUTH_MODE=subscription`, which means the bot uses your
**Claude Code subscription** (whatever account you signed into via
`claude auth login`) and **does not bill per-token via the API**. To prevent
accidents the bot actively scrubs `ANTHROPIC_API_KEY` from its environment at
startup, even if you have one in `.env`.

If you'd rather pay per-token, set `ANTHROPIC_AUTH_MODE=api_key` and fill in
`ANTHROPIC_API_KEY`. The bot will refuse to start if the key is missing in
that mode (saves you from a silently-broken setup).

`ANTHROPIC_AUTH_MODE=auto` is the historical behavior: API key if present,
else subscription. Handy for setups that flip between the two.

### 5. Point at a project that can push to origin

The `/approve` flow runs `git push origin <branch>` at the end of every task.
Make sure your workspace has an `origin` configured:

```powershell
cd C:\dev\your-project
git remote -v  # should list origin
```

If origin is missing, `/approve` refuses the task with a friendly error before
agents spend any tokens.

Add the workspace to `.env`:

```ini
AGENT_WORKSPACES=C:\dev\your-project
AGENT_WORKSPACE_MODE=allowlist
```

Or run in `open` mode to allow `/workspace <path>` to point anywhere:

```ini
AGENT_WORKSPACE_MODE=open
```

### 6. Run

```powershell
python -m agent_hub
```

Or double-click `run.bat`.

In Telegram, find your bot, send `/start`. The PM will greet you.

## Talking to the team

- **Default messages** go to the PM.
- **Address anyone directly** with `@architect`, `@fullstack`, `@reviewer`,
  `@research`, `@designer`, `@qa`.

### Slash commands

**Session**
- `/start` — wake the bot, show team + workspace
- `/agents` — list the team
- `/to <agent>` — set who you're talking to (sticky)
- `/reset <agent|all>` — clear an agent's memory
- `/workspace [path]` — show or change the project folder
- `/projects` — recently-used project folders
- `/whoami` — your Telegram user ID + version

**Tasks**
- `/tasks` — list active tasks
- `/task <id>` — show one task in detail (status, owner, spend, recent events)
- `/approve <id>` — approve a design gate (creates worktree + hands to fullstack)
- `/reject <id> <reason>` — reject a design and send the architect back with feedback
- `/cancel <id>` — abort a running task (drops pending handoffs, resets agent session)
- `/resume <id>` — resume a stale or blocked task
- `/status` — orchestrator health snapshot (queue depth, gates, sessions, spend)
- `/budget [amount|off]` — view, set, or disable the cumulative spend cap
- `/help` — list all commands

## Budget control

By default there's no cap and you pay for whatever the agents consume. Set one:

```
/budget 5.00
```

When cumulative spend exceeds the cap, the orchestrator pauses handoff dispatch
and DMs you once. Raise it (`/budget 10.00`) or remove it (`/budget off`) to
resume. Active tasks are NOT cancelled — they just pause between turns.

Check current spend any time with `/status` or `/budget`.

## What's safe

- `AGENT_WORKSPACES` (in `allowlist` mode) is an explicit allowlist. Agents
  can't read or write outside those folders.
- Bash commands run through the SDK's permission system; tighten or loosen per
  role via each YAML's `allowed_tools`.
- The bot only responds to `TELEGRAM_ALLOWED_USER_ID`.
- Direct commits on `main` are not the workflow — every task gets its own
  `task/<id>-<slug>` branch in an isolated worktree.

## Surviving restart

- The single-instance lock at `data/agent_hub.lock` prevents two orchestrators
  from running at once (psutil liveness check; stale locks are stolen safely).
- On boot, the orchestrator releases any handoff rows claimed by a dead
  previous process (scoped to non-terminal tasks — DONE/BLOCKED audit trail is
  preserved).
- Design gates already DM'd to you stay quiet on restart (`gates.notified_at`).
- Stale in-flight tasks get a one-shot DM with `/resume` prompts.
- Each (agent, task_id) has a persistent SDK session UUID, so agents re-attach
  to their prior conversation rather than starting fresh.

## Concurrency

`HANDOFF_WORKER_COUNT` (default 3) controls how many handoffs dispatch in
parallel. Per-(agent, task_id) ordering is preserved by the runner pool lock,
so workers safely race on the queue.

## Where things live

```
agent-hub/
├── agent_hub/                        # The Python package
│   ├── agents/
│   │   ├── roles/                    # YAML role definitions — edit freely
│   │   ├── runner.py                 # Claude Agent SDK wrapper + client pool
│   │   ├── runner_options.py         # ClaudeAgentOptions builder
│   │   └── session_store.py          # (agent, task_id) → session_id persistence
│   ├── mcp_server/                   # MCP tools the agents call
│   │   └── tools/                    #   tasks.* handoff gate.* worktree.*
│   ├── orchestrator/                 # Routing + tick loops + push
│   ├── telegram_bot/                 # Telegram frontend + command handlers
│   └── tasks/                        # Repositories (tasks, gates, handoffs, worktrees)
├── data/                             # SQLite + lock file (gitignored)
├── docs/superpowers/                 # Specs, plans, runbooks
└── tests/                            # Unit, integration, smoke (267 + smoke)
```

## Running the smoke test

```powershell
$env:RUN_SMOKE_TESTS = "1"
.venv\Scripts\python.exe -m pytest tests/smoke/ -v -s
```

Drives the full chain (PM → architect → /approve → fullstack → reviewer → QA →
push) on a temporary repo using Haiku-pinned roles. Real Anthropic API calls,
~$0.05–0.10 per run, completes in ~3 minutes.
