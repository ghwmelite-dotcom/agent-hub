# First-time deployment runbook

Step-by-step setup for a fresh agent_hub install. Follow top-to-bottom; each
step is self-contained.

## 1. Prerequisites

- **Python 3.12+** on PATH (`python --version` to check; 3.14 is what we
  develop against).
- **Git** on PATH.
- A **Telegram account** for the chat surface.
- An **Anthropic auth path**: either `ANTHROPIC_API_KEY` or a working
  `claude auth login` session.
- A **project directory with `origin` configured**. The bot's full loop ends
  with `git push origin <branch>`; without a remote, `/approve` will refuse
  the task before agents start.

## 2. Telegram bot creation

1. Open Telegram, search for `@BotFather` (blue check on the account).
2. `/newbot` → display name → username ending in `bot`.
3. Save the token (`7891234567:AAH-xxxxx...`).
4. Message `@userinfobot` to get your numeric user ID. The bot will refuse
   anyone whose ID doesn't match `TELEGRAM_ALLOWED_USER_ID`.

## 3. Install

```powershell
cd C:\dev\agent-hub
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Configure `.env`

```powershell
copy .env.example .env
notepad .env
```

Required:

```ini
TELEGRAM_BOT_TOKEN=7891234567:AAH-...
TELEGRAM_ALLOWED_USER_ID=12345678
```

Anthropic auth — pick one:

```ini
# Recommended for Claude Code subscribers — no per-token API billing.
# The bot scrubs ANTHROPIC_API_KEY from its env at startup so the SDK
# can't fall back to per-token billing. Run `claude auth login` first
# so the OAuth tokens are cached.
ANTHROPIC_AUTH_MODE=subscription

# Or pay per-token via the API:
# ANTHROPIC_AUTH_MODE=api_key
# ANTHROPIC_API_KEY=sk-ant-api03-...
```

Workspace allowlist (point at a project that has `origin`):

```ini
AGENT_WORKSPACES=C:\dev\your-project
AGENT_WORKSPACE_MODE=allowlist
```

Optional knobs (defaults shown):

```ini
HANDOFF_WORKER_COUNT=3          # concurrent handoff dispatchers
GATE_REMINDER_HOURS=24          # re-DM a still-pending design gate this often
STUCK_TURN_THRESHOLD=12         # DM when a task hits this many turns w/o status change
LOG_LEVEL=INFO
```

## 5. Verify the workspace can ship

```powershell
cd C:\dev\your-project
git remote -v
```

You should see `origin <url>` for both fetch and push. If you don't:

```powershell
git remote add origin git@github.com:you/your-project.git
git push -u origin main   # confirm credentials work
```

## 6. Run the bot

```powershell
cd C:\dev\agent-hub
python -m agent_hub
```

Or `run.bat`. The console will log:

```
db.ready path=...\data\agent_hub.db
orchestrator started
telegram bot polling
```

## 7. First conversation in Telegram

- Find your bot in Telegram, send `/start`.
- PM replies with the team and current workspace.
- File a tiny task to verify the loop:

  ```
  @pm append a line "hello from agent_hub" to README.md
  ```

- Watch for the architect's design DM.
- Reply `/approve <task_id>` (the ID is in the gate DM).
- Watch fullstack → reviewer → QA flow through.
- Final DM: `✅ Task #N done. Pushed branch task/N-... to origin.`

If the push DM says "failed", check:
- `git remote -v` in your workspace shows `origin`
- Your credential helper / SSH key works for the remote

## 8. Set a budget

The first run with no cap will spend whatever the agents need (usually $0.05–
$0.50 for a small task). To cap it:

```
/budget 5.00
```

When you hit the cap, dispatch pauses and you get a one-time DM. Raise the
cap or `/budget off` to keep going.

## 9. Daily-use commands

| Command | Effect |
|---|---|
| `@pm <ask>` | File a new task |
| `/tasks` | List active tasks |
| `/task <id>` | One task's detail + spend + recent events |
| `/status` | Health snapshot (queue, gates, sessions, spend) |
| `/approve <id>` | Approve a pending design gate |
| `/reject <id> <reason>` | Reject design, send architect back |
| `/cancel <id>` | Abort a running task |
| `/resume <id>` | Resume a stale/blocked task |
| `/budget [n|off]` | View, set, or remove the cap |
| `/workspace <path>` | Switch projects at runtime |
| `/help` | Full command list |

## 10. Surviving restart

Just kill the process and restart with `python -m agent_hub`. The orchestrator
will:

- Re-acquire the single-instance lock (`data/agent_hub.lock`).
- Release any handoff rows the dead process had claimed (non-terminal tasks).
- Re-attach each (agent, task_id) to its persistent SDK session UUID.
- DM you with the list of stale in-flight tasks + `/resume` prompts.

In-flight gate-ready DMs you already saw stay quiet — no re-notification spam.

## Common gotchas

- **"Cannot approve — no workspace is configured"**: `AGENT_WORKSPACES` is
  empty AND no `/workspace <path>` has been issued. Set one of the two.
- **"`origin` is not configured"**: `/approve` refuses to start agents on a
  repo with no remote. Run `git remote add origin <url>` and re-approve.
- **API balance**: if you're using `ANTHROPIC_API_KEY` and the account has
  no credit, every agent turn fails with a 4xx. Top up at console.anthropic.com.
- **Bot says "This bot is private"**: your Telegram user ID doesn't match
  `TELEGRAM_ALLOWED_USER_ID`. The error message echoes your actual ID.

## Where to go next

- `docs/superpowers/runbooks/haiku-smoke.md` — end-to-end test on real API
- `docs/superpowers/specs/` — design specs for each plan
- `agent_hub/agents/roles/*.yaml` — edit role prompts to tune behavior
