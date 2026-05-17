# Agent Hub

A team of specialized Claude agents you manage over Telegram. The agents run
locally on your machine using the Claude Agent SDK (the same engine that powers
Claude Code), so they have real filesystem, git, and shell access to any
project folder you whitelist.

## The team

| Role            | What they do                                                              |
| --------------- | ------------------------------------------------------------------------- |
| **Senior PM**   | Default chat partner. Owns the PRD, breaks work into tasks, runs standups, decides what ships. |
| **Architect**   | Designs implementation approach before code is written.                   |
| **Implementer** | Writes code. Can run in parallel for independent tasks.                   |
| **Reviewer**    | Independent second pass on diffs, design choices, migrations.             |
| **Researcher**  | Web search, library evaluation, documentation reading.                    |
| **QA**          | Runs tests, validates against spec, takes screenshots.                    |

Roles are defined in `agent_hub/agents/roles/*.yaml` — edit the system prompts
and tool allowlists without touching code.

## Quick start

### 1. Create a Telegram bot

1. Open Telegram, search for `@BotFather` (the official one has a blue check).
2. Send `/newbot`, give it a display name and a unique username ending in `bot`.
3. Save the token BotFather gives you (looks like `7891234567:AAH-xxxxx...`).

### 2. Find your Telegram user ID

Message `@userinfobot` on Telegram — it replies with your numeric ID. The bot
will refuse messages from anyone else, so only you can drive your agents.

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

Fill in `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, and (optionally)
`ANTHROPIC_API_KEY`. If you're already signed into Claude Code on this machine,
the SDK will reuse those credentials and no API key is needed.

### 5. Run

```powershell
python -m agent_hub
```

Or just double-click `run.bat`.

Open Telegram, find your bot, send `/start`. The PM will greet you.

## Talking to the team

- **Default messages** go to the PM.
- **Address an agent directly** with `@architect`, `@impl`, `@reviewer`, `@research`, `@qa`.
- **Slash commands:**
  - `/start` — wake the bot, show help
  - `/agents` — list the team
  - `/status` — what each agent is currently working on
  - `/standup` — PM polls every agent and summarizes
  - `/workspace <path>` — switch which project folder agents work in
  - `/reset <agent>` — clear an agent's memory and start fresh

## Autonomy levels

Set `PM_AUTONOMY` in `.env`:

- `low` — every step requires explicit approval. Slowest, safest.
- `medium` (default) — PM proposes a plan, waits for your OK, then executes.
- `high` — PM runs workstreams autonomously. Only asks on destructive ops or big architectural decisions.

## Where things live

```
agent-hub/
├── agent_hub/              # The Python package
│   ├── agents/
│   │   ├── roles/          # YAML role definitions — edit freely
│   │   └── runner.py       # Claude Agent SDK wrapper
│   ├── orchestrator/       # Routing + approvals
│   └── telegram_bot/       # Telegram frontend
├── data/                   # SQLite + agent state (gitignored)
└── logs/                   # Runtime logs (gitignored)
```

## Safety notes

- `AGENT_WORKSPACES` is an explicit allowlist — agents can't read or write
  outside those folders.
- Bash commands run through the Agent SDK's permission system; you can require
  approval for any tool in each role's YAML.
- The bot only responds to your `TELEGRAM_ALLOWED_USER_ID`.
