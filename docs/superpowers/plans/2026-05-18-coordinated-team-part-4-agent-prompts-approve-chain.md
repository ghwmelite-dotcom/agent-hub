# Coordinated team — Part 4: Agent prompts + /approve chain + Haiku smoke

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent_hub usable end-to-end via Telegram. Wire `/approve` to enqueue the follow-on handoff (worktree create + fullstack), teach every code-writing agent how to use the MCP tools (`tasks.*`, `handoff`, `gate.*`, `worktree.*`), expand each role's `allowed_tools` allowlist to include the orchestration tools, and validate the whole chain with a Haiku-pinned end-to-end smoke test.

**Architecture:** Three orthogonal changes plus a smoke gate. (1) `handle_approve` gains optional `repo_root` / `worktrees_root` keyword args; when both are set, it creates the worktree and enqueues a handoff to `fullstack-engineer`. (2) Each role YAML's `system_prompt` gets a "Tools and workflow" section that teaches the canonical flow, and `allowed_tools` gains the `mcp__agent_hub__*` entries it needs. (3) A Tier 3 smoke test in `tests/smoke/` that drives a real Haiku-pinned PM + architect + fullstack + reviewer + QA through a trivial feature on a tmp git repo, gated by `RUN_SMOKE_TESTS=1`.

**Tech Stack:** Python 3.14, claude-agent-sdk (real Haiku for smoke), aiosqlite, pytest. No new dependencies.

**Source spec:** `docs/superpowers/specs/2026-05-17-coordinated-agent-team-design.md` (§4.7 agent role updates, §5 flows, §7 Tier 3 smoke).

**Source plan dependencies:** Plans 1, 2, 3 — all merged to `main`.

**Not in this plan (deferred to Plan 5):**
- Spend cap + `/budget` command
- Stuck-loop detection
- Gate-timeout reminders (24h / 7d)
- `_notified_gates` persistence across restart
- Stuck-claim recovery for `handoff_queue` orphan claims

---

## What "done" looks like for Plan 4

A user opens Telegram, types `@pm add a comment to README explaining the test harness`, and watches:
1. PM responds (creates task, decides medium-sized, hands off to architect).
2. Architect produces a design comment + requests the gate.
3. Bot DMs the user with the design and `/approve` hint.
4. User types `/approve <id>`.
5. Worktree is created on `task/<id>-<slug>`, fullstack-engineer is summoned with the task context.
6. Fullstack-engineer writes code, commits, hands off to reviewer.
7. Reviewer LGTMs, hands off to QA.
8. QA runs tests, marks task done.
9. Branch is pushed to `origin`.
10. User gets a DM with the branch name.

After Plan 4: the above works on Haiku for a trivial task (the smoke test). Real production runs use whatever model the role YAML pins (Sonnet/Opus per the existing config).

Still deferred: a runaway agent could rack up cost (no spend cap), a stuck agent could spin forever (no stuck-loop detection), and a gate could sit pending without reminders. Those are Plan 5.

---

## File structure produced by this plan

```
agent_hub/
  telegram_bot/
    commands/
      approve_cmd.py             # MODIFY: add repo_root/worktrees_root kwargs + worktree+handoff logic
    bot.py                       # MODIFY: pass repo_root + worktrees_root into _on_approve
  agents/
    roles/
      pm.yaml                    # MODIFY: prompt + allowed_tools
      architect.yaml             # MODIFY: prompt + allowed_tools
      fullstack-engineer.yaml    # MODIFY: prompt + allowed_tools
      implementer.yaml           # MODIFY: prompt + allowed_tools
      reviewer.yaml              # MODIFY: prompt + allowed_tools
      qa.yaml                    # MODIFY: prompt + allowed_tools
      researcher.yaml            # MODIFY: light prompt + allowed_tools
      senior-uiux-designer.yaml  # MODIFY: light prompt + allowed_tools

tests/
  test_commands_approve.py       # MODIFY: add tests for worktree+handoff path
  smoke/
    __init__.py                  # CREATE
    test_smoke_haiku.py          # CREATE: gated end-to-end smoke
  test_role_prompts.py           # CREATE: static contract tests on each role's allowed_tools

docs/
  superpowers/
    runbooks/
      haiku-smoke.md             # CREATE: how to run the smoke test
```

---

## Conventions used in every task

- **TDD pattern:** failing test → verify fail → minimal impl → verify pass → commit.
- **Test runner:** `.\.venv\Scripts\python.exe -m pytest` from repo root.
- **Commit style:** Conventional Commits.
- **Prompt additions are ADDITIVE, never destructive:** when modifying a role YAML, append to the existing `system_prompt` rather than replacing it. The existing prompt body carries character/voice; the new section adds capability.
- **MCP tool name format:** `mcp__agent_hub__tasks_create`, `mcp__agent_hub__handoff`, etc. The `mcp__<server-name>__<tool-name>` pattern is how the Claude Agent SDK exposes MCP tools, where dots in the registered tool name become underscores in the namespaced form. Each role's `allowed_tools` lists these explicit names.

---

## Task 0: handle_approve creates worktree and enqueues fullstack handoff

**Files:**
- Modify: `agent_hub/telegram_bot/commands/approve_cmd.py`
- Modify: `tests/test_commands_approve.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_commands_approve.py`:

```python
import subprocess
from pathlib import Path

from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.worktree_repo import WorktreeRepository


@pytest.fixture
def git_repos(tmp_path: Path):
    """Bare remote + local clone with one commit on main."""
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    local = tmp_path / "repo"
    subprocess.check_call(["git", "clone", str(remote), str(local)])
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=local)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=local)
    (local / "x.txt").write_text("x\n")
    subprocess.check_call(["git", "add", "x.txt"], cwd=local)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=local)
    subprocess.check_call(["git", "push", "origin", "main"], cwd=local)
    return local


@pytest.mark.asyncio
async def test_approve_with_repo_root_creates_worktree_and_handoff(deps, git_repos, tmp_path):
    repo, gates, db = deps
    task = await repo.create(title="add health", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    worktrees_root = tmp_path / "worktrees"

    reply = await handle_approve(
        task_id=task.id,
        db_path=db.path,
        repo_root=git_repos,
        worktrees_root=worktrees_root,
    )

    # Gate resolved, status flipped to READY
    assert await gates.status(task_id=task.id, kind="design") == "approved"
    assert (await repo.get(task.id)).status == TaskStatus.READY

    # Worktree created and recorded
    wt_repo = WorktreeRepository(db.path)
    wt_row = await wt_repo.get_by_task(task.id)
    assert wt_row is not None
    assert Path(wt_row.path).exists()
    assert wt_row.branch == f"task/{task.id}-add-health"

    # Handoff to fullstack-engineer enqueued
    queue = HandoffQueue(db.path)
    pending = await queue.pending()
    assert any(h.to_agent == "fullstack-engineer" and h.task_id == task.id for h in pending)

    # Reply mentions the next step
    assert "fullstack" in reply.lower() or "ready" in reply.lower()


@pytest.mark.asyncio
async def test_approve_without_repo_root_is_gate_only(deps):
    """Backward-compat path: no repo_root means just resolve gate + flip status.
    This is the shape used by existing tests."""
    repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    reply = await handle_approve(task_id=task.id, db_path=db.path)

    # Still works — no worktree creation attempted.
    assert (await repo.get(task.id)).status == TaskStatus.READY
    assert "approved" in reply.lower()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_approve.py -v`
Expected: the new `test_approve_with_repo_root_creates_worktree_and_handoff` test FAILS — `handle_approve` doesn't accept `repo_root`/`worktrees_root` kwargs.

- [ ] **Step 3: Update handle_approve**

Replace `agent_hub/telegram_bot/commands/approve_cmd.py` with:

```python
"""Pure handler for /approve <id> — resolves the pending design gate
and advances the task from design_review to ready.

When repo_root + worktrees_root are provided (production path), also
creates the per-task git worktree and enqueues the follow-on handoff
to fullstack-engineer. When omitted (test path), stops at status=ready.

Kept pure (no PTB import) so it can be unit-tested without a bot.
The Telegram glue (extracting task_id from the message, sending the
reply) lives in agent_hub/telegram_bot/bot.py.
"""

from __future__ import annotations

from pathlib import Path

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


async def handle_approve(
    *,
    task_id: int,
    db_path: Path,
    repo_root: Path | None = None,
    worktrees_root: Path | None = None,
) -> str:
    """Resolve the design gate (if any) and flip the task to ready.

    Production path (both repo_root and worktrees_root provided):
    - Resolves gate as approved
    - Flips status from design_review → ready
    - Creates a git worktree at <worktrees_root>/<task_id>/ on branch task/<id>-<slug>
    - Enqueues handoff to fullstack-engineer with task context

    Test/minimal path (repo_root or worktrees_root missing):
    - Resolves gate as approved
    - Flips status from design_review → ready
    - Returns

    Returns a human-readable reply suitable for posting back to the
    user's chat.
    """
    repo = TaskRepository(db_path)
    gates = GateRepository(db_path)

    task = await repo.get(task_id)
    if task is None:
        return f"Task #{task_id} not found."

    status = await gates.status(task_id=task_id, kind="design")
    if status == "none":
        return f"Task #{task_id} has no pending design gate to approve."
    if status != "pending":
        return f"Task #{task_id} gate is already {status}."

    await gates.resolve(task_id=task_id, kind="design", resolution="approved")
    try:
        await repo.update(task_id, status=TaskStatus.READY)
    except InvalidTransition as exc:
        return f"Approved the gate but couldn't advance status: {exc}"

    # If we don't have the workspace info, stop here. Tests use this path.
    if repo_root is None or worktrees_root is None:
        return f"✅ Task #{task_id} approved — moving to ready."

    # Production path: create worktree + handoff to fullstack-engineer.
    from agent_hub.worktree_manager import WorktreeManager
    manager = WorktreeManager(
        repo_root=repo_root,
        worktrees_root=worktrees_root,
        db_path=db_path,
    )
    try:
        wt = await manager.create(task_id=task_id, title=task.title, base_branch="main")
    except RuntimeError as exc:
        return (
            f"✅ Task #{task_id} approved, but worktree creation failed: {exc}\n"
            f"Status is at ready — investigate manually."
        )

    queue = HandoffQueue(db_path)
    await queue.enqueue(
        task_id=task_id,
        from_agent="user",
        to_agent="fullstack-engineer",
        message=(
            f"Design approved by user. Implement per the architect's design comment on the task. "
            f"Your worktree is at {wt['path']} on branch {wt['branch']}. "
            f"Start by calling tasks.get({task_id}) to read the design."
        ),
    )
    return (
        f"✅ Task #{task_id} approved — fullstack-engineer is on it.\n"
        f"Branch: `{wt['branch']}`"
    )
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_approve.py -v`
Expected: 5 PASS (3 existing + 2 new).

- [ ] **Step 5: Full suite**

Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -3`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/telegram_bot/commands/approve_cmd.py tests/test_commands_approve.py
git commit -m "feat(approve): create worktree and handoff to fullstack on approval"
```

---

## Task 1: bot.py passes repo_root + worktrees_root to /approve

**Files:** Modify `agent_hub/telegram_bot/bot.py`.

- [ ] **Step 1: Locate the _on_approve handler in bot.py**

Run: `Grep` for `_on_approve` in `agent_hub/telegram_bot/bot.py`. Read its body — it currently calls `handle_approve(task_id=..., db_path=db_path)`.

- [ ] **Step 2: Modify _on_approve to pass workspace info**

Update the handler to pass `repo_root` and `worktrees_root`:

```python
    async def _on_approve(update, context):
        if not context.args:
            await update.effective_chat.send_message("Usage: /approve <id>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.effective_chat.send_message("Task id must be an integer.")
            return

        # Workspace info is needed for the worktree creation + handoff.
        repo_root = settings.default_workspace
        worktrees_root = repo_root.parent / "worktrees" if repo_root else None

        reply = await handle_approve(
            task_id=task_id,
            db_path=db_path,
            repo_root=repo_root,
            worktrees_root=worktrees_root,
        )
        await update.effective_chat.send_message(reply)
```

(The other `_on_*` handlers are untouched.)

- [ ] **Step 3: Smoke test imports**

Run: `.\.venv\Scripts\python.exe -c "from agent_hub.telegram_bot.bot import build_application; print('ok')"`
Expected: `ok`.

Run the full suite:
Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -3`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add agent_hub/telegram_bot/bot.py
git commit -m "feat(bot): wire repo_root + worktrees_root into /approve handler"
```

---

## Task 2: Static contract test for role allowed_tools

**Files:**
- Create: `tests/test_role_prompts.py`

This test asserts the contract that future role updates must satisfy:
each role's allowed_tools list contains the MCP tools its workflow needs.

- [ ] **Step 1: Write the test**

Create `tests/test_role_prompts.py`:

```python
"""Static contract tests on each role's allowed_tools.

These tests describe the canonical capability matrix for the team.
Adding a new role: add a row to EXPECTED. Adding a new MCP tool that
a role needs: update the role's row. Tests fail if a role is missing
a tool the workflow requires.
"""

import pytest

from agent_hub.agents.registry import AgentRegistry


# (role_name, list of MCP tool names the role MUST have in allowed_tools)
EXPECTED_MCP_TOOLS: dict[str, list[str]] = {
    "pm": [
        "mcp__agent_hub__tasks_create",
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_list",
        "mcp__agent_hub__tasks_update",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__handoff",
    ],
    "architect": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__tasks_update",
        "mcp__agent_hub__gate_request",
    ],
    "fullstack-engineer": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_update",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__worktree_path",
        "mcp__agent_hub__handoff",
    ],
    "implementer": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_update",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__worktree_path",
        "mcp__agent_hub__handoff",
    ],
    "reviewer": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__handoff",
    ],
    "qa": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_comment",
        "mcp__agent_hub__tasks_update",
        "mcp__agent_hub__handoff",
    ],
    "researcher": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_comment",
    ],
    "senior-uiux-designer": [
        "mcp__agent_hub__tasks_get",
        "mcp__agent_hub__tasks_comment",
    ],
}


@pytest.fixture
def registry() -> AgentRegistry:
    return AgentRegistry.load()


@pytest.mark.parametrize("role_name,required_tools", list(EXPECTED_MCP_TOOLS.items()))
def test_role_allowed_tools_contains_required_mcp_tools(registry, role_name, required_tools):
    role = registry.get(role_name)
    missing = [t for t in required_tools if t not in role.allowed_tools]
    assert not missing, (
        f"Role {role_name!r} is missing required MCP tools: {missing}. "
        f"Current allowed_tools: {role.allowed_tools}"
    )


@pytest.mark.parametrize("role_name", list(EXPECTED_MCP_TOOLS.keys()))
def test_role_system_prompt_mentions_mcp_tools(registry, role_name):
    """Sanity check that the role's prompt references the task system —
    catches the case where allowed_tools was updated but the prompt
    wasn't taught the workflow."""
    role = registry.get(role_name)
    prompt = role.system_prompt.lower()
    assert any(keyword in prompt for keyword in (
        "tasks.create", "tasks.get", "handoff", "gate.request",
    )), f"Role {role_name!r} prompt doesn't mention any orchestration tool."
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_role_prompts.py -v`
Expected: many tests FAIL — current role YAMLs don't have the MCP tools in `allowed_tools` and prompts don't mention them.

This is intentional. The next 8 tasks (3-10) make these tests pass, one role at a time.

- [ ] **Step 3: Commit**

```bash
git add tests/test_role_prompts.py
git commit -m "test(roles): assert canonical allowed_tools + prompt mentions per role"
```

---

## Task 3: PM role — prompt + allowed_tools

**Files:** Modify `agent_hub/agents/roles/pm.yaml`.

- [ ] **Step 1: Read current pm.yaml**

Run: `cat agent_hub/agents/roles/pm.yaml`. Note the existing `name`, `display_name`, `aliases`, `model`, `allowed_tools`, and `system_prompt`.

- [ ] **Step 2: Update allowed_tools**

Replace the `allowed_tools` list with one that adds the MCP entries:

```yaml
allowed_tools:
  - Read
  - Grep
  - Glob
  - WebSearch
  - WebFetch
  - mcp__agent_hub__tasks_create
  - mcp__agent_hub__tasks_get
  - mcp__agent_hub__tasks_list
  - mcp__agent_hub__tasks_update
  - mcp__agent_hub__tasks_comment
  - mcp__agent_hub__handoff
```

(Preserve whatever existing entries are in the file; just add the MCP ones. Don't add Write/Edit/Bash — PM doesn't write code.)

- [ ] **Step 3: Append to system_prompt**

Append the following BLOCK to the existing `system_prompt`. Do NOT delete what's there — add this section after the existing content (within the same `|` multi-line string):

```
  ## Tools and workflow

  You drive a team of Claude agents via a task ledger. Every user
  request becomes a task you create with tasks.create. You never edit
  code yourself — your job is to size the work, decide who handles it,
  and hand it off.

  When a user sends you a request:

  1. Call `tasks.create(title=..., description=..., origin_chat_id=...)`
     where origin_chat_id is taken from the routed message context
     (the orchestrator prepends `[task #N, from @user]` on handoffs;
     for the first user message, the chat_id is implicit and you
     should ask the user for it if it's not clear — typically it's
     in the route prefix).

  2. Size the work:
     - **Small** (single-file change, ~30 min): call
       `tasks.update(task_id, status="in_progress", owner="fullstack-engineer")`
       and `handoff(to_agent="fullstack-engineer", task_id=..., message="<scope>")`.
       Skip the architect.
     - **Medium** (multi-file feature, half a day, one PR): call
       `tasks.update(task_id, status="planning", owner="architect")` then
       `handoff(to_agent="architect", task_id=..., message="<scope>")`.
     - **Large** (multi-day, multiple PRs): decompose into 2-5 sub-tasks
       via repeated `tasks.create(parent_id=<root_id>, ...)`, then start
       the first leaf with a handoff.

  3. Reply briefly to the user in chat: "Filed as task #N. Architect is
     taking the design pass." or similar. Don't over-explain.

  4. You do NOT call gate.request — that's the architect's verb.

  ## Examples of sizing

  - "Add a comment to the README" → small → fullstack directly
  - "Add a /health endpoint that pings D1" → medium → architect first
  - "Add Stripe billing" → large → decompose into pricing page,
    checkout endpoint, webhook handler, customer portal

  Be tight. The user shouldn't wait for you — they should see the task
  filed and an agent already working on it.
```

- [ ] **Step 4: Verify YAML still parses**

Run: `.\.venv\Scripts\python.exe -c "from agent_hub.agents.registry import AgentRegistry; r = AgentRegistry.load(); print(r.get('pm').name)"`
Expected: prints `pm`.

- [ ] **Step 5: Run the pm contract tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_role_prompts.py -v -k "pm"`
Expected: both pm tests PASS (one for allowed_tools, one for prompt mentions).

- [ ] **Step 6: Commit**

```bash
git add agent_hub/agents/roles/pm.yaml
git commit -m "feat(roles): teach PM the task ledger + handoff workflow"
```

---

## Task 4: Architect role — prompt + allowed_tools

**Files:** Modify `agent_hub/agents/roles/architect.yaml`.

- [ ] **Step 1: Update allowed_tools**

Add to the existing `allowed_tools` list:

```yaml
  - mcp__agent_hub__tasks_get
  - mcp__agent_hub__tasks_comment
  - mcp__agent_hub__tasks_update
  - mcp__agent_hub__gate_request
```

Preserve existing entries (Read, Grep, Glob, WebSearch, WebFetch). Do NOT add Write/Edit/Bash — architect doesn't write code.

- [ ] **Step 2: Append to system_prompt**

Append this block to the existing prompt:

```
  ## Tools and workflow

  You receive design requests via handoffs from the PM. The orchestrator
  prepends `[task #N, from @pm] <message>` to every handoff — extract
  the task_id from that prefix.

  When you receive a design request:

  1. Call `tasks.get(task_id)` to read the full task state (description,
     prior comments, any earlier design attempts).

  2. Read the existing codebase in the workspace to ground your design.
     You have Read/Grep/Glob/WebFetch — use them. Don't speculate about
     code you haven't read.

  3. Produce a concrete design covering:
     - which files change, which are new
     - the public interface (function signatures, route paths, DB columns)
     - the trade-offs you weighed
     - the risk you would flag to the implementer

  4. Post your design as a comment on the task:
     `tasks.comment(task_id, body=<the full design text>)`.

  5. Request the human gate:
     `gate.request(task_id, kind="design", summary="<one-sentence summary>")`.

  6. Move the task to design_review:
     `tasks.update(task_id, status="design_review")`.

  7. STOP. Do not hand off. Do not implement. The user reviews the
     design via Telegram and either /approves (orchestrator hands the
     task to fullstack-engineer) or /rejects with feedback (orchestrator
     hands the task back to you with the rejection reason in the new
     handoff message).

  On rejection, you receive a new handoff with the user's feedback.
  Revise the design (read the rejection comment, address the concerns,
  post a NEW design comment, gate.request again).

  ## What good designs look like

  - Concrete file paths and function names, not "add a function that..."
  - Trade-offs surfaced explicitly: "option A vs option B; recommending A because..."
  - Risk flagged for the implementer: "watch the auth middleware order"
  - Short. Two paragraphs of architecture + a 5-line bullet list of files.

  You are NOT the implementer. You hand off design, not code.
```

- [ ] **Step 3: Verify + test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_role_prompts.py -v -k "architect"`
Expected: both architect tests PASS.

- [ ] **Step 4: Commit**

```bash
git add agent_hub/agents/roles/architect.yaml
git commit -m "feat(roles): teach architect the design + gate.request workflow"
```

---

## Task 5: Fullstack-engineer role — prompt + allowed_tools

**Files:** Modify `agent_hub/agents/roles/fullstack-engineer.yaml`.

- [ ] **Step 1: Update allowed_tools**

Add to the existing `allowed_tools` list (preserve Read/Write/Edit/Bash/Grep/Glob/WebSearch/WebFetch):

```yaml
  - mcp__agent_hub__tasks_get
  - mcp__agent_hub__tasks_update
  - mcp__agent_hub__tasks_comment
  - mcp__agent_hub__worktree_path
  - mcp__agent_hub__handoff
```

- [ ] **Step 2: Append to system_prompt**

Append this block:

```
  ## Tools and workflow

  You receive implementation requests via handoffs after the architect's
  design is approved by the user. The orchestrator prepends
  `[task #N, from @user] <message>` to your handoff message, which
  includes the path to your worktree.

  When you receive an implementation handoff:

  1. Call `tasks.get(task_id)` to read the design (the architect's
     design lives as a comment on the task).

  2. Call `worktree.path(task_id)` to confirm your working directory.
     The orchestrator already set your `cwd` to that path, but verify
     it matches — if not, something is wrong; report it and stop.

  3. Move yourself to in_progress:
     `tasks.update(task_id, status="in_progress", owner="fullstack-engineer")`.

  4. Implement the feature. Read existing code, write new code, follow
     the conventions in the file you're modifying. Don't invent libraries
     or APIs — verify by reading.

  5. Commit your work with a descriptive Conventional Commits message:
     ```
     git add <files>
     git commit -m "feat(<scope>): <what changed>"
     ```

  6. Hand off to reviewer:
     `tasks.update(task_id, status="review")` then
     `handoff(to_agent="reviewer", task_id=task_id, message="<what you did + what to check>")`.

  ## When you get bounced back by reviewer

  Reviewer can hand back to you with blocker comments. Treat their
  feedback as design input — read the comments, fix the issues, commit,
  hand off to reviewer again. Don't argue in the comments — just fix.

  ## What you do NOT do

  - Do not push to origin — that happens automatically when QA marks
    the task done.
  - Do not call gate.request — that's the architect's verb.
  - Do not update status to "done" — that's QA's call.

  Stay inside your worktree. The orchestrator picks the right cwd for
  your turn; if you ever find yourself in `C:\dev\baobab` instead of
  `C:\dev\worktrees\<task_id>\`, stop and report — something is wrong.
```

- [ ] **Step 3: Verify + test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_role_prompts.py -v -k "fullstack"`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add agent_hub/agents/roles/fullstack-engineer.yaml
git commit -m "feat(roles): teach fullstack-engineer the implement + handoff-to-reviewer workflow"
```

---

## Task 6: Implementer role — prompt + allowed_tools

**Files:** Modify `agent_hub/agents/roles/implementer.yaml`.

The Implementer role overlaps with Fullstack-engineer but exists for narrower bug-fix / single-file tasks. Apply the same tool updates and a slightly tighter prompt.

- [ ] **Step 1: Update allowed_tools**

Add to existing `allowed_tools`:

```yaml
  - mcp__agent_hub__tasks_get
  - mcp__agent_hub__tasks_update
  - mcp__agent_hub__tasks_comment
  - mcp__agent_hub__worktree_path
  - mcp__agent_hub__handoff
```

- [ ] **Step 2: Append to system_prompt**

```
  ## Tools and workflow

  You receive narrow-scope handoffs — usually single-file bug fixes,
  small refactors, or specific changes the PM sized as small enough
  to skip the architect. Your turn:

  1. `tasks.get(task_id)` to read the request.
  2. `worktree.path(task_id)` to confirm cwd.
  3. `tasks.update(task_id, status="in_progress", owner="implementer")`.
  4. Make the change. Commit. Don't expand scope.
  5. `tasks.update(task_id, status="review")` and
     `handoff(to_agent="reviewer", task_id=task_id, message="<one-line summary>")`.

  If you discover the scope is bigger than expected (you'd be touching
  more than a couple of files, or the change requires real design
  judgment), stop and hand back to PM:
  `handoff(to_agent="pm", task_id=task_id, message="Scope expanded — needs architect.")`.

  Don't push, don't call gate.request, don't mark done.
```

- [ ] **Step 3: Verify + test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_role_prompts.py -v -k "implementer"`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add agent_hub/agents/roles/implementer.yaml
git commit -m "feat(roles): teach implementer the narrow-scope handoff workflow"
```

---

## Task 7: Reviewer role — prompt + allowed_tools

**Files:** Modify `agent_hub/agents/roles/reviewer.yaml`.

- [ ] **Step 1: Update allowed_tools**

Add to existing:

```yaml
  - mcp__agent_hub__tasks_get
  - mcp__agent_hub__tasks_comment
  - mcp__agent_hub__handoff
```

- [ ] **Step 2: Append to system_prompt**

```
  ## Tools and workflow

  You receive review requests from fullstack-engineer or implementer.
  The orchestrator already set your cwd to the worktree.

  1. `tasks.get(task_id)` to see the design and recent events.
  2. Read the diff: `git diff main..HEAD` (or `git log -1 --stat -p`)
     to see exactly what changed in this worktree.
  3. Review for correctness first, then security, then operational
     impact, then style. Categorize as blockers vs nits.
  4. Post your review as a comment:
     `tasks.comment(task_id, body=<your review>)`.

  Then decide:

  - **Approved** (no blockers, possibly some nits): hand off to QA.
    `handoff(to_agent="qa", task_id=task_id, message="Approved. Nits non-blocking.")`.

  - **Blockers** found: hand back to the implementer with the blockers
    listed. `handoff(to_agent="fullstack-engineer", task_id=task_id, message="Blockers: ...")`.
    Do NOT change task status — let fullstack flip it back to in_progress.

  Don't push, don't mark done — QA does that.

  ## Tone

  Direct but constructive. Don't manufacture problems. If something is
  fine, say so. Distinguish blockers from nits clearly so the
  implementer knows what they must fix vs what's optional polish.
```

- [ ] **Step 3: Verify + test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_role_prompts.py -v -k "reviewer"`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add agent_hub/agents/roles/reviewer.yaml
git commit -m "feat(roles): teach reviewer the diff-review + handoff workflow"
```

---

## Task 8: QA role — prompt + allowed_tools

**Files:** Modify `agent_hub/agents/roles/qa.yaml`.

- [ ] **Step 1: Update allowed_tools**

Add to existing:

```yaml
  - mcp__agent_hub__tasks_get
  - mcp__agent_hub__tasks_comment
  - mcp__agent_hub__tasks_update
  - mcp__agent_hub__handoff
```

- [ ] **Step 2: Append to system_prompt**

```
  ## Tools and workflow

  You receive tasks from the reviewer after they've approved the diff.
  Your cwd is the task's worktree.

  1. `tasks.get(task_id)` to read the design + recent events.
  2. Look in the repo for the test command — check `package.json`,
     `pyproject.toml`, `Makefile`, or the project's README. Run the
     relevant test suite, type checker, and linter.
  3. Post a summary comment:
     `tasks.comment(task_id, body=<pass/fail + what you ran>)`.

  Then:

  - **Pass**: `tasks.update(task_id, status="done")`. The orchestrator
    automatically pushes the branch to origin and DMs the user.

  - **Fail**: hand back to fullstack:
    `handoff(to_agent="fullstack-engineer", task_id=task_id, message="Test failures: ...")`.
    Do NOT change the status — let fullstack flip it back to in_progress.

  ## What you actually run

  - Python project: `pytest` (look for `pyproject.toml` `[tool.pytest.ini_options]`)
  - Node project: `npm test` or whatever `package.json:scripts.test` says
  - Multi-package: `turbo test` or `pnpm -r test`
  - If there's no test command, run the type checker and linter as
    proof the change at least compiles cleanly.

  Be specific in your comment about which commands you ran. "tests
  pass" is not enough — say "ran `npm test` (97/97 pass) and
  `npm run typecheck` (clean)".
```

- [ ] **Step 3: Verify + test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_role_prompts.py -v -k "qa"`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add agent_hub/agents/roles/qa.yaml
git commit -m "feat(roles): teach QA the test + tasks.update(done) workflow"
```

---

## Task 9: Researcher role — light prompt + allowed_tools

**Files:** Modify `agent_hub/agents/roles/researcher.yaml`.

Researcher is a read-only role for codebase/web research. Light update — just gain visibility into tasks.

- [ ] **Step 1: Update allowed_tools**

Add to existing:

```yaml
  - mcp__agent_hub__tasks_get
  - mcp__agent_hub__tasks_comment
```

(Researcher does NOT get handoff — they're a read-only consultant. The agent that invoked them is responsible for the next step.)

- [ ] **Step 2: Append to system_prompt**

```
  ## Tools and workflow

  When invoked from a task context (handoff message includes
  `[task #N, ...]`), your findings should land as a comment on the
  task so future agents can read your research:

  1. `tasks.get(task_id)` to understand what's being researched.
  2. Do the research (Read, Grep, Glob, WebSearch, WebFetch).
  3. `tasks.comment(task_id, body=<your findings, citations, recommendation>)`.

  You do not hand off — the agent who routed work to you decides the
  next step based on your findings.
```

- [ ] **Step 3: Verify + test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_role_prompts.py -v -k "researcher"`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add agent_hub/agents/roles/researcher.yaml
git commit -m "feat(roles): teach researcher to post findings as task comments"
```

---

## Task 10: Senior UI/UX Designer role — light prompt + allowed_tools

**Files:** Modify `agent_hub/agents/roles/senior-uiux-designer.yaml`.

- [ ] **Step 1: Update allowed_tools**

Add to existing:

```yaml
  - mcp__agent_hub__tasks_get
  - mcp__agent_hub__tasks_comment
```

(Designer doesn't hand off — like researcher, they're a consultant whose output is a design comment on the task.)

- [ ] **Step 2: Append to system_prompt**

```
  ## Tools and workflow

  When invoked from a task context, post your design spec as a comment
  so the engineer who implements it can read it:

  1. `tasks.get(task_id)` to understand the UX problem.
  2. Read existing UI / design tokens / components.
  3. Produce your spec (layout, tokens, motion, accessibility,
     interaction states).
  4. `tasks.comment(task_id, body=<full spec with file:line citations>)`.

  You do not hand off. The PM or the engineer reading your spec
  decides what happens next.
```

- [ ] **Step 3: Verify + test**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_role_prompts.py -v -k "senior-uiux"`
Expected: PASS.

Run full test_role_prompts:
`.\.venv\Scripts\python.exe -m pytest tests/test_role_prompts.py -v`
Expected: ALL pass (16 — 2 per role × 8 roles).

- [ ] **Step 4: Commit**

```bash
git add agent_hub/agents/roles/senior-uiux-designer.yaml
git commit -m "feat(roles): teach UI/UX designer to post specs as task comments"
```

---

## Task 11: Smoke test scaffolding

**Files:**
- Create: `tests/smoke/__init__.py` (empty)
- Create: `tests/smoke/test_smoke_haiku.py` (skeleton)

- [ ] **Step 1: Create the empty package init**

Create `tests/smoke/__init__.py` (empty file).

- [ ] **Step 2: Create the skeleton test gated by env var**

Create `tests/smoke/test_smoke_haiku.py`:

```python
"""End-to-end smoke test with a real Haiku-pinned agent set.

Gated behind RUN_SMOKE_TESTS=1 because it:
- Makes real Claude API calls (~$0.10–0.50 per run)
- Takes 30s–2min to complete
- Requires ANTHROPIC_API_KEY

Run manually before tagging a release:
    set RUN_SMOKE_TESTS=1
    .\\.venv\\Scripts\\python.exe -m pytest tests/smoke/ -v -s
"""

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SMOKE_TESTS") != "1",
    reason="set RUN_SMOKE_TESTS=1 to run smoke tests (real Claude API calls)",
)


def test_skeleton():
    """Sanity check that the gate works."""
    assert True
```

- [ ] **Step 3: Run unskipped**

Run: `.\.venv\Scripts\python.exe -m pytest tests/smoke/ -v`
Expected: 1 skipped.

- [ ] **Step 4: Run with gate active**

PowerShell:
```powershell
$env:RUN_SMOKE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/smoke/test_smoke_haiku.py::test_skeleton -v
Remove-Item env:RUN_SMOKE_TESTS
```

Expected: 1 passed when gate is active.

- [ ] **Step 5: Commit**

```bash
git add tests/smoke/__init__.py tests/smoke/test_smoke_haiku.py
git commit -m "test(smoke): add Haiku smoke gate with skip-by-default"
```

---

## Task 12: Haiku end-to-end smoke test

**Files:** Modify `tests/smoke/test_smoke_haiku.py`.

This is the load-bearing validation of Plan 4. It spins up the orchestrator with REAL Claude SDK clients on a Haiku-pinned override of every role, then files a trivial task and watches the chain complete.

- [ ] **Step 1: Write the smoke test**

Replace `tests/smoke/test_smoke_haiku.py` with:

```python
"""End-to-end smoke test with a real Haiku-pinned agent set.

Gated behind RUN_SMOKE_TESTS=1 because it:
- Makes real Claude API calls (~$0.10–0.50 per run)
- Takes 30s–2min to complete
- Requires ANTHROPIC_API_KEY

Run manually before tagging a release:
    set RUN_SMOKE_TESTS=1
    .\\.venv\\Scripts\\python.exe -m pytest tests/smoke/ -v -s
"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from agent_hub.agents import AgentRegistry, AgentRunner
from agent_hub.agents.registry import AgentRole
from agent_hub.config import Settings
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from tests.fakes.fake_surface import FakeMessageSurface


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SMOKE_TESTS") != "1",
    reason="set RUN_SMOKE_TESTS=1 to run smoke tests (real Claude API calls)",
)


def _haiku_pinned_registry(original: AgentRegistry) -> AgentRegistry:
    """Return a registry where every role's model is overridden to
    Haiku for the smoke run."""
    pinned = []
    for r in original.all():
        pinned.append(AgentRole(
            name=r.name,
            display_name=r.display_name,
            aliases=r.aliases,
            model="claude-haiku-4-5-20251001",
            allowed_tools=r.allowed_tools,
            system_prompt=r.system_prompt,
        ))
    return AgentRegistry(pinned)


def _seed_git_repo(repo_root: Path) -> None:
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo_root)
    subprocess.check_call(["git", "config", "user.name", "Smoke"], cwd=repo_root)
    subprocess.check_call(["git", "config", "user.email", "smoke@example.com"], cwd=repo_root)
    (repo_root / "README.md").write_text("# Smoke project\n\nA tiny test target.\n")
    subprocess.check_call(["git", "add", "README.md"], cwd=repo_root)
    subprocess.check_call(["git", "commit", "-m", "initial"], cwd=repo_root)


@pytest.mark.asyncio
async def test_haiku_end_to_end_simple_task(tmp_path: Path):
    """File a trivial task, watch PM → architect → user-/approve →
    fullstack → reviewer → QA → done.

    The task is intentionally tiny so Haiku can complete it: "Add a
    line to README.md that says 'Hello agent team.'".

    This test does NOT cover real Telegram — it drives the orchestrator
    directly and uses FakeMessageSurface to capture DMs. The real
    Telegram surface is validated manually by running the bot.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    # Workspace setup: tmp git repo we treat as the project.
    repo_root = tmp_path / "smoke-project"
    repo_root.mkdir()
    _seed_git_repo(repo_root)
    worktrees_root = tmp_path / "worktrees"

    db_path = tmp_path / "agent_hub.db"
    os.environ["AGENT_HUB_DB"] = str(db_path)

    db = Database(db_path)
    await db.init()
    repo = TaskRepository(db_path)
    queue = HandoffQueue(db_path)

    settings = Settings(
        telegram_bot_token="smoke-dummy",
        telegram_allowed_user_id=1,
        database_path=db_path,
        agent_workspaces=[repo_root],
    )

    registry = _haiku_pinned_registry(AgentRegistry.load())
    runner = AgentRunner(settings=settings, registry=registry)
    surface = FakeMessageSurface()

    orch = Orchestrator(
        registry=registry,
        runner=runner,
        db=db,
        surface=surface,
        repo_root=repo_root,
    )

    try:
        await orch.start()

        # File the task. In production the PM creates this via
        # tasks.create after a Telegram message; for the smoke we just
        # seed the DB directly and start with a handoff to PM.
        task = await repo.create(
            title="Add hello line to README",
            description=(
                "Append a single line `Hello agent team.` to README.md "
                "in this repo. This is a smoke test — keep the change "
                "minimal."
            ),
            origin_chat_id=999,
        )
        await queue.enqueue(
            task_id=task.id, from_agent="user", to_agent="pm",
            message=f"User filed task #{task.id}: append 'Hello agent team.' to README.md",
        )

        # Wait up to 4 minutes for the task to reach done. The handoff
        # loop ticks every 250ms — we just poll the DB.
        deadline = asyncio.get_event_loop().time() + 240  # 4 min
        final_status = None
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2.0)
            current = await repo.get(task.id)
            if current is None:
                continue
            if current.status == TaskStatus.DONE:
                final_status = TaskStatus.DONE
                break
            if current.status == TaskStatus.BLOCKED:
                final_status = TaskStatus.BLOCKED
                break

            # Auto-approve any pending design gate that appears.
            from agent_hub.tasks.gates import GateRepository
            from agent_hub.telegram_bot.commands.approve_cmd import handle_approve
            gates = GateRepository(db_path)
            gate_status = await gates.status(task_id=task.id, kind="design")
            if gate_status == "pending":
                await handle_approve(
                    task_id=task.id,
                    db_path=db_path,
                    repo_root=repo_root,
                    worktrees_root=worktrees_root,
                )

        assert final_status == TaskStatus.DONE, (
            f"Task did not reach done; final status: {final_status}. "
            f"DMs captured: {[m for _, m in surface.sent]}"
        )

        # Verify a worktree branch was created (worktree may have been
        # cleaned up if the orchestrator pushed and cleaned — that's
        # the desired behavior, so check git for the branch's existence
        # in the main repo's refs).
        proc = subprocess.run(
            ["git", "branch", "--list"],
            cwd=repo_root,
            capture_output=True, text=True,
        )
        assert proc.returncode == 0
        # Branch like task/<id>-add-hello-line-to-readme should appear
        # (or have been pushed; for a local-only repo without a remote
        # the branch will still be there).
        assert f"task/{task.id}" in proc.stdout, (
            f"No task branch found in git output: {proc.stdout}"
        )

    finally:
        await orch.stop()
        await runner.shutdown()
        os.environ.pop("AGENT_HUB_DB", None)
```

- [ ] **Step 2: Run with gate active (manually)**

PowerShell:
```powershell
$env:RUN_SMOKE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/smoke/test_smoke_haiku.py -v -s
Remove-Item env:RUN_SMOKE_TESTS
```

Expected: PASS within 4 minutes. If it FAILS, debug:
- Check `surface.sent` for what the agents said
- Check `repo.events(task_id)` for the audit trail
- Check `queue.pending()` and `gates.status(...)` for which step stuck

This is a manual gate. If it fails, you've found a real bug in Plan 4 — fix and re-run.

- [ ] **Step 3: Commit (test code only, not a run)**

```bash
git add tests/smoke/test_smoke_haiku.py
git commit -m "test(smoke): Haiku end-to-end task lifecycle"
```

---

## Task 13: Runbook for the Haiku smoke test

**Files:** Create `docs/superpowers/runbooks/haiku-smoke.md`.

- [ ] **Step 1: Create the runbook**

Create the directory if needed and write `docs/superpowers/runbooks/haiku-smoke.md`:

```markdown
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

\`\`\`powershell
$env:RUN_SMOKE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/smoke/test_smoke_haiku.py -v -s
Remove-Item env:RUN_SMOKE_TESTS
\`\`\`

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
\`\`\`

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/runbooks/haiku-smoke.md
git commit -m "docs(smoke): runbook for the Haiku end-to-end smoke test"
```

---

## Task 14: Full suite + parallel verification

**Files:** none.

- [ ] **Step 1: Serial run**

Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -10`
Expected: all pass. Total ≈ Plan 3 baseline (171) + ~20 new tests (16 role contract + 2 approve + 1 smoke gate + 1 skeleton) = ~190.

- [ ] **Step 2: Parallel run**

Run: `.\.venv\Scripts\python.exe -m pytest -n auto 2>&1 | tail -5`
Expected: same total, all pass.

- [ ] **Step 3: Optionally run the smoke (manual)**

This step is informational — the smoke test is gated and won't run in CI. If you have an API key and want to validate end-to-end:

PowerShell:
```powershell
$env:RUN_SMOKE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/smoke/test_smoke_haiku.py -v -s
Remove-Item env:RUN_SMOKE_TESTS
```

Watch the agents work. If it passes, Plan 4 is real.

- [ ] **Step 4: No commit if everything green**

If isolation issues surface under -n auto, fix the fixture (not the assertion), commit, and re-run.

---

## Self-review

**Spec coverage:**
- §4.7 agent role updates — Tasks 3-10 cover PM, architect, fullstack-engineer, implementer, reviewer, qa, researcher, senior-uiux-designer ✓
- §4.7 PM intake → decompose → assign — Task 3 ✓
- §4.7 architect's design + gate.request flow — Task 4 ✓
- §4.7 fullstack-engineer's worktree.path + handoff to reviewer — Task 5 ✓
- §4.7 reviewer's approval → handoff to qa OR blockers → handoff to fullstack — Task 7 ✓
- §4.7 QA's tasks.update(status=done) — Task 8 ✓
- §5 Flow A /approve → worktree + handoff to fullstack — Tasks 0, 1 ✓
- §7 Tier 3 Haiku smoke — Tasks 11, 12 ✓
- Runbook for the smoke — Task 13 ✓

**Placeholder scan:** none. Every step has runnable code or runnable commands.

**Type consistency:**
- `handle_approve` signature: kwargs `task_id`, `db_path`, optional `repo_root`, `worktrees_root` — consistent in Tasks 0, 1, and the smoke in Task 12.
- MCP tool names in `allowed_tools`: `mcp__agent_hub__tasks_create`, `mcp__agent_hub__tasks_get`, `mcp__agent_hub__tasks_list`, `mcp__agent_hub__tasks_update`, `mcp__agent_hub__tasks_comment`, `mcp__agent_hub__handoff`, `mcp__agent_hub__gate_request`, `mcp__agent_hub__worktree_path` — used consistently across Tasks 2-10.
- The contract test in Task 2 is the single source of truth for which tools each role gets; if the implementer subagent for any of Tasks 3-10 sees a mismatch, the contract test catches it.

**Known sequencing notes:**
- Task 2 (contract test) intentionally lands BEFORE Tasks 3-10 so each role update has a failing test to drive against.
- Tasks 3-10 can in principle be parallelized; subagent-driven-development serializes them which is fine.
- Task 12 (the actual smoke logic) depends on Tasks 3-10 (all roles updated) and Tasks 0-1 (/approve wired). Don't run Task 12 against an incomplete role set — it will hang or fail.

**Out-of-scope items confirmed deferred to Plan 5:**
- Spend cap + `/budget` command
- Stuck-loop detection
- Gate-timeout reminders (24h / 7d)
- `_notified_gates` persistence
- Stuck-claim recovery for handoff_queue
- Free-form Telegram text routing through `classify_freeform_message` (the helper exists from Plan 3 but isn't wired into bot.py's message handler yet — that's also Plan 5 polish)
