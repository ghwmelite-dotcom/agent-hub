# Haiku smoke test runbook

The Haiku smoke test exercises the full agent_hub chain end-to-end with
a real Claude SDK on cheap Haiku models. Run it before tagging a release
or after any change to:

- agent role prompts or allowed_tools
- The /approve chain
- The orchestrator's handoff / gate / push / epic logic
- The runner's per-(agent, task) pool or worktree resolution

## Prerequisites

- `.venv` with `pip install -r requirements.txt` complete
- `ANTHROPIC_API_KEY` set in your environment (you'll spend ~$0.10–0.50)
- Git on PATH
- Working internet connection to api.anthropic.com

## Running

PowerShell:

```powershell
$env:RUN_SMOKE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/smoke/test_smoke_haiku.py -v -s
Remove-Item env:RUN_SMOKE_TESTS
```

The `-s` flag lets you see structlog output as the agents work, which
is useful for debugging when it fails.

Expected duration: 30s–4min depending on Haiku response time and how
many handoffs the agents make.

## What success looks like

- Test passes within 4 minutes
- Task reaches status `done`
- A `task/<id>-add-hello-line-to-readme` branch exists in the smoke
  project's git history with a commit appending "Hello agent team." to
  README.md
- `surface.sent` contains at least: a gate-ready DM, an approve reply,
  and a done DM

## When it fails

1. **Stuck at planning/design_review:** the architect didn't call
   gate.request. Look at the architect's last turn in
   `repo.events(task_id)` — does it mention gate.request?
2. **Stuck at ready:** the /approve auto-approver in the test isn't
   firing, or the worktree creation fails. Check
   `worktree.get_by_task(task_id)`.
3. **Stuck at in_progress / review:** fullstack-engineer or reviewer
   didn't hand off. Read the comments on the task — did the agent
   describe what it did without actually calling `handoff`?
4. **Failed assertions about the branch:** something pushed (or didn't
   push) when it shouldn't have. Check the orchestrator's push log.

The most common Plan 4 failure mode is an agent that "describes" what
it should do in prose instead of actually calling the tool. Re-read
the role's system prompt — the workflow section should be more
explicit about MUST CALL vs MAY DISCUSS.

## Cost expectations

- Pure Haiku run: ~$0.10
- With one or two retries from bad transitions: ~$0.30
- A run that runs to the 4-min timeout: ~$0.50

If a run costs more than $1, kill it and investigate — that's a
stuck-loop indicator.
