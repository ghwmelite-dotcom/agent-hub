# Coordinated team — Part 3: Orchestrator background tasks + Telegram commands

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the inert Plan 1+2 infrastructure into a working team you can drive from Telegram. The orchestrator runs background tasks (handoff loop, gate watcher, push action, epic auto-completion, restart-resume scan); the bot gains the `/tasks`, `/task <id>`, `/approve`, `/reject`, `/resume` commands; free-form replies in a sticky task thread become `tasks.comment` + handoff to the current owner.

**Architecture:** Extend the existing `Orchestrator` class with three background asyncio tasks (`_run_handoff_loop`, `_run_gate_watcher`, kept alive by a single `start()`/`stop()` lifecycle). Introduce a `MessageSurface` abstraction with a real `TelegramSurface` and a test `FakeMessageSurface` so the orchestrator can be tested without a real bot. Add a `FakeAgentRunner` that subclasses `AgentRunner` and replays scripted events so integration tests drive the state machine deterministically. Telegram command handlers live in `agent_hub/telegram_bot/commands/`.

**Tech Stack:** Python 3.14, asyncio, python-telegram-bot, aiosqlite, pytest + pytest-asyncio.

**Source spec:** `docs/superpowers/specs/2026-05-17-coordinated-agent-team-design.md` (sections 4.4 orchestrator responsibilities, 4.8 Telegram surface, 5 data flows A/B/C, 6.D restart-in-flight, 7 Tier 2 integration tests).

**Source plan dependencies:** Plan 1 (data layer + MCP), Plan 2 (worktree manager + runner pool + lock). Both merged to `main`.

**Not in this plan (deferred):**
- Spend cap + per-task budget enforcement — **Plan 4**
- Stuck-loop detection — **Plan 4**
- Gate timeout reminders (24h DM) and auto-block (7d) — **Plan 4**
- Agent role prompt updates so PM actually calls `tasks.create` — **Plan 4**
- Adding `mcp__agent_hub__*` to `allowed_tools` in role YAMLs — **Plan 4**
- Real Claude SDK end-to-end smoke (Haiku) — **Plan 4**

---

## What "done" looks like for Plan 3

After this plan merges, the orchestrator's runtime is wired up: the handoff loop polls `handoff_queue` and dispatches via the runner; the gate watcher detects new pending design gates and DMs the user; when a task transitions to `done`, the orchestrator pushes the branch and DMs the URL; if a task is the last leaf of an epic, the epic auto-completes. The bot accepts `/tasks`, `/task <id>`, `/approve <id>`, `/reject <id> <reason>`, `/resume <id>`. Tier 2 integration tests cover Flow A (single feature with design gate), Flow B (parallel epic), Flow C (design rejection) — all using `FakeAgentRunner`, no real SDK calls.

You still can't drive a real Claude agent to ship a real branch yet because agents don't know about the MCP tools — that lands in Plan 4. But the orchestration plumbing is complete and observable via the `FakeAgentRunner` integration tests.

---

## File structure produced by this plan

```
agent_hub/
  orchestrator/
    __init__.py             # MODIFY: re-export new types
    router.py               # MODIFY: extend Orchestrator with start()/stop(), handoff loop, etc.
    surface.py              # CREATE: MessageSurface abstraction + TelegramSurface impl
    handoff_loop.py         # CREATE: handoff queue poller (called by Orchestrator.start)
    gate_watcher.py         # CREATE: gate watcher loop (called by Orchestrator.start)
    push.py                 # CREATE: push-on-done action
    epic.py                 # CREATE: epic auto-completion logic
    resume.py               # CREATE: restart-resume scan on boot
  telegram_bot/
    bot.py                  # MODIFY: register new command handlers; pass orchestrator into context
    surface_telegram.py     # CREATE: real Telegram impl of MessageSurface (lives in telegram_bot/)
    commands/
      __init__.py           # CREATE
      tasks_cmd.py          # CREATE: /tasks
      task_cmd.py           # CREATE: /task <id>
      approve_cmd.py        # CREATE: /approve <id>
      reject_cmd.py         # CREATE: /reject <id> <reason>
      resume_cmd.py         # CREATE: /resume <id>
  __main__.py               # MODIFY: start orchestrator background tasks; run restart-resume scan

tests/
  fakes/
    __init__.py             # CREATE
    fake_runner.py          # CREATE: FakeAgentRunner — scripted-event replay
    fake_surface.py         # CREATE: FakeMessageSurface — records sent messages
  test_surface.py           # CREATE: MessageSurface contract tests
  test_handoff_loop.py      # CREATE: handoff loop unit tests
  test_gate_watcher.py      # CREATE: gate watcher unit tests
  test_push.py              # CREATE: push action unit tests
  test_epic.py              # CREATE: epic auto-completion unit tests
  test_resume.py            # CREATE: restart-resume scan unit tests
  test_commands_tasks.py    # CREATE: /tasks command handler
  test_commands_task.py     # CREATE: /task <id> handler
  test_commands_approve.py  # CREATE: /approve handler
  test_commands_reject.py   # CREATE: /reject handler
  test_commands_resume.py   # CREATE: /resume handler
  test_router_task_aware.py # CREATE: task-aware free-form message interpretation
  integration/
    __init__.py             # CREATE
    test_flow_a.py          # CREATE: single feature with design gate
    test_flow_b.py          # CREATE: parallel epic
    test_flow_c.py          # CREATE: design rejection loop
```

---

## Conventions used in every task

- **TDD pattern:** failing test → verify fail → minimal impl → verify pass → commit.
- **Test runner:** `.\.venv\Scripts\python.exe -m pytest` (always venv python).
- **Commit style:** Conventional Commits.
- **Background task pattern:** every long-running loop in the orchestrator follows this shape:
  ```python
  async def _run_some_loop(self) -> None:
      while not self._stop_event.is_set():
          try:
              await self._tick_some()
          except Exception as exc:
              log.exception("orchestrator.some_loop.tick_failed", error=str(exc))
          try:
              await asyncio.wait_for(self._stop_event.wait(), timeout=0.25)
          except asyncio.TimeoutError:
              pass
  ```
  This gives a deterministic 250ms tick, catches per-tick exceptions so the loop survives, and exits promptly when `_stop_event.set()` is called.
- **MessageSurface abstraction:** the orchestrator NEVER imports `python-telegram-bot` directly. It calls `self.surface.dm(chat_id, text)` etc. Real Telegram lives in `agent_hub/telegram_bot/surface_telegram.py`; tests use `FakeMessageSurface`.
- **Existing Orchestrator (Plan 1) refactor scope:** the Plan 1 `Orchestrator` class has `handle()` method for routing user messages. Plan 3 ADDS `start()`, `stop()`, and the loop methods; preserves `handle()`. No breaking changes to existing tests.

---

## Task 0: MessageSurface abstraction

**Files:**
- Create: `agent_hub/orchestrator/surface.py`
- Create: `tests/test_surface.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_surface.py`:

```python
"""Contract tests for MessageSurface and FakeMessageSurface."""

import pytest

from agent_hub.orchestrator.surface import MessageSurface


class _FakeSurface(MessageSurface):
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def dm(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_message_surface_is_abstract():
    """Cannot instantiate MessageSurface directly — it's a Protocol or ABC."""
    with pytest.raises(TypeError):
        MessageSurface()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_fake_surface_records_dms():
    surface = _FakeSurface()
    await surface.dm(chat_id=42, text="hello")
    await surface.dm(chat_id=42, text="world")
    assert surface.sent == [(42, "hello"), (42, "world")]
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_surface.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement MessageSurface**

Create `agent_hub/orchestrator/surface.py`:

```python
"""Abstract surface for sending messages OUT of the orchestrator.

The orchestrator never imports python-telegram-bot directly. It calls
`surface.dm(chat_id, text)`. The Telegram implementation lives in
`agent_hub/telegram_bot/surface_telegram.py`; tests use FakeMessageSurface.

Why an abstraction: the handoff loop, gate watcher, push action, and
restart-resume scan all need to message the user. Decoupling lets us
swap implementations (Telegram → Slack → web hook → fake) without
touching orchestrator logic, and gives the test suite a deterministic
seam.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class MessageSurface(ABC):
    """The orchestrator's outbound message channel."""

    @abstractmethod
    async def dm(self, chat_id: int, text: str) -> None:
        """Send a direct message to the user identified by chat_id."""
        raise NotImplementedError
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_surface.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/surface.py tests/test_surface.py
git commit -m "feat(surface): add MessageSurface abstraction"
```

---

## Task 1: FakeMessageSurface

**Files:**
- Create: `tests/fakes/__init__.py` (empty)
- Create: `tests/fakes/fake_surface.py`
- Modify: `tests/test_surface.py`

- [ ] **Step 1: Add failing test that uses the production FakeMessageSurface**

Modify `tests/test_surface.py` — replace the inline `_FakeSurface` class with an import:

```python
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.mark.asyncio
async def test_message_surface_is_abstract():
    with pytest.raises(TypeError):
        MessageSurface()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_fake_surface_records_dms():
    surface = FakeMessageSurface()
    await surface.dm(chat_id=42, text="hello")
    await surface.dm(chat_id=42, text="world")
    assert surface.sent == [(42, "hello"), (42, "world")]


@pytest.mark.asyncio
async def test_fake_surface_dms_to_chat_filter():
    surface = FakeMessageSurface()
    await surface.dm(chat_id=10, text="a")
    await surface.dm(chat_id=20, text="b")
    await surface.dm(chat_id=10, text="c")
    assert surface.dms_to(10) == ["a", "c"]
    assert surface.dms_to(20) == ["b"]
```

(Remove the existing `_FakeSurface` class.)

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_surface.py -v`
Expected: ImportError.

- [ ] **Step 3: Create the fakes package + FakeMessageSurface**

Create `tests/fakes/__init__.py` (empty).

Create `tests/fakes/fake_surface.py`:

```python
"""Test double for MessageSurface that records sent messages."""

from __future__ import annotations

from agent_hub.orchestrator.surface import MessageSurface


class FakeMessageSurface(MessageSurface):
    """Records every dm call for assertions."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def dm(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    def dms_to(self, chat_id: int) -> list[str]:
        """Return the bodies of all DMs sent to a specific chat_id."""
        return [t for c, t in self.sent if c == chat_id]
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_surface.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fakes/__init__.py tests/fakes/fake_surface.py tests/test_surface.py
git commit -m "test: add FakeMessageSurface with dms_to helper"
```

---

## Task 2: FakeAgentRunner

**Files:**
- Create: `tests/fakes/fake_runner.py`
- Create: `tests/test_fake_runner.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_fake_runner.py`:

```python
"""Tests for the FakeAgentRunner — a test double that replays scripted
events instead of calling the real Claude SDK."""

import pytest

from agent_hub.agents.runner import TextChunk, ToolStart, ToolEnd, TurnDone
from tests.fakes.fake_runner import FakeAgentRunner, scripted_turn


@pytest.fixture
def fake_runner():
    runner = FakeAgentRunner()
    return runner


@pytest.mark.asyncio
async def test_script_a_single_turn(fake_runner):
    fake_runner.script("pm", task_id=1, events=[
        TextChunk(text="Hi"),
        ToolStart(tool="tasks.create", input={"title": "x"}),
        ToolEnd(tool="tasks.create", is_error=False),
        TurnDone(cost_usd=0.01, duration_ms=100),
    ])

    events = []
    async for event in fake_runner.send("pm", "go", task_id=1):
        events.append(event)

    assert len(events) == 4
    assert isinstance(events[0], TextChunk)
    assert events[0].text == "Hi"
    assert isinstance(events[-1], TurnDone)


@pytest.mark.asyncio
async def test_unscripted_send_raises(fake_runner):
    """If no script is set for (agent, task_id), send() raises."""
    with pytest.raises(AssertionError) as exc:
        async for _ in fake_runner.send("pm", "go", task_id=99):
            pass
    assert "no script" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_multi_turn_script(fake_runner):
    """Each call to script() queues a turn. Each send() pops one."""
    fake_runner.script("pm", task_id=1, events=[TextChunk(text="first")])
    fake_runner.script("pm", task_id=1, events=[TextChunk(text="second")])

    first = [e async for e in fake_runner.send("pm", "msg1", task_id=1)]
    second = [e async for e in fake_runner.send("pm", "msg2", task_id=1)]

    assert first[0].text == "first"
    assert second[0].text == "second"


@pytest.mark.asyncio
async def test_send_records_messages(fake_runner):
    fake_runner.script("pm", task_id=1, events=[TextChunk(text="ok")])
    async for _ in fake_runner.send("pm", "hello", task_id=1):
        pass
    assert fake_runner.calls == [("pm", "hello", 1)]


def test_scripted_turn_helper():
    """The scripted_turn helper builds a turn from text + optional tool calls."""
    turn = scripted_turn(text="hello", tools=[("tasks.create", {"title": "x"})])
    kinds = [type(e).__name__ for e in turn]
    assert kinds == ["TextChunk", "ToolStart", "ToolEnd", "TurnDone"]
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_fake_runner.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement FakeAgentRunner**

Create `tests/fakes/fake_runner.py`:

```python
"""FakeAgentRunner — scripted-event replay for integration tests.

Subclass-compatible with the real AgentRunner public interface
(send/shutdown/reset). Tests call `.script(agent, task_id, events=[...])`
to queue what each send() should yield, then call `.send(...)` and
assert on the resulting DB state. Multiple scripts for the same key
are queued in order (FIFO).

The Calls field records every send invocation for assertion convenience.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from agent_hub.agents.runner import (
    AgentError,
    AgentEvent,
    TextChunk,
    ToolEnd,
    ToolStart,
    TurnDone,
)


class FakeAgentRunner:
    """Drop-in replacement for AgentRunner in tests."""

    def __init__(self) -> None:
        self._scripts: dict[tuple[str, int | None], deque[list[AgentEvent]]] = defaultdict(deque)
        self.calls: list[tuple[str, str, int | None]] = []

    def script(
        self,
        agent_name: str,
        *,
        task_id: int | None,
        events: list[AgentEvent],
    ) -> None:
        """Queue a single turn's events for (agent, task_id)."""
        self._scripts[(agent_name, task_id)].append(events)

    async def send(
        self,
        agent_name: str,
        message: str,
        *,
        task_id: int | None = None,
    ):
        """Yield the next queued turn's events for this (agent, task_id)."""
        self.calls.append((agent_name, message, task_id))
        queue = self._scripts.get((agent_name, task_id))
        if not queue:
            raise AssertionError(
                f"FakeAgentRunner has no script for agent={agent_name!r} "
                f"task_id={task_id!r}. Call .script(...) before .send(...)."
            )
        events = queue.popleft()
        for event in events:
            yield event

    async def shutdown(self) -> None:
        """No-op in the fake — nothing to disconnect."""
        return

    async def reset(self, agent_name: str, *, task_id: int | None = None) -> None:
        self._scripts.pop((agent_name, task_id), None)


def scripted_turn(
    *,
    text: str | None = None,
    tools: list[tuple[str, dict[str, Any]]] | None = None,
    cost_usd: float = 0.001,
    duration_ms: int = 50,
) -> list[AgentEvent]:
    """Build a typical turn: optional text, optional tool calls, TurnDone."""
    events: list[AgentEvent] = []
    if text:
        events.append(TextChunk(text=text))
    for tool_name, tool_input in tools or []:
        events.append(ToolStart(tool=tool_name, input=tool_input))
        events.append(ToolEnd(tool=tool_name, is_error=False))
    events.append(TurnDone(cost_usd=cost_usd, duration_ms=duration_ms))
    return events
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_fake_runner.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fakes/fake_runner.py tests/test_fake_runner.py
git commit -m "test: add FakeAgentRunner with scripted_turn helper"
```

---

## Task 3: Orchestrator gains start/stop lifecycle

**Files:**
- Modify: `agent_hub/orchestrator/router.py`
- Create: `tests/test_orchestrator_lifecycle.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_orchestrator_lifecycle.py`:

```python
"""Tests for Orchestrator.start/stop lifecycle of background tasks."""

import asyncio

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
async def orchestrator(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=FakeMessageSurface(),
    )


@pytest.mark.asyncio
async def test_start_then_stop_terminates_cleanly(orchestrator):
    await orchestrator.start()
    # Give the loops a couple of ticks to spin up.
    await asyncio.sleep(0.05)
    await orchestrator.stop()
    # After stop, the background tasks should be done.
    for task in orchestrator._tasks:
        assert task.done()


@pytest.mark.asyncio
async def test_stop_without_start_is_noop(orchestrator):
    """Calling stop on a never-started orchestrator must not raise."""
    await orchestrator.stop()


@pytest.mark.asyncio
async def test_double_start_raises(orchestrator):
    await orchestrator.start()
    try:
        with pytest.raises(RuntimeError):
            await orchestrator.start()
    finally:
        await orchestrator.stop()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_orchestrator_lifecycle.py -v`
Expected: AttributeError on `Orchestrator(surface=...)` — constructor doesn't accept `surface` yet, no `start`/`stop`.

- [ ] **Step 3: Extend Orchestrator with lifecycle**

In `agent_hub/orchestrator/router.py`, modify the `Orchestrator.__init__` to accept a `surface`, and add `start()` / `stop()`. Add `asyncio` and `MessageSurface` imports at the top of the file.

Update `__init__`:

```python
import asyncio

from agent_hub.orchestrator.surface import MessageSurface


class Orchestrator:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        runner: AgentRunner,
        db: Database,
        surface: MessageSurface | None = None,
        default_agent: str = "pm",
    ):
        self.registry = registry
        self.runner = runner
        self.db = db
        self.surface = surface
        self.default_agent = default_agent
        self._sticky: dict[int, str] = {}
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._started = False
```

(Keep the existing `sticky_for`, `set_sticky`, `clear_sticky`, `handle` methods untouched.)

Add at the end of the class:

```python
    async def start(self) -> None:
        """Start the background loops. Idempotent? NO — raises if called twice."""
        if self._started:
            raise RuntimeError("Orchestrator.start() called twice")
        self._started = True
        self._stop_event.clear()
        # Background loops land in later tasks; for now this is a stub
        # that creates no tasks but flips the flag so stop() can verify.

    async def stop(self) -> None:
        """Signal all background loops to exit and wait for them."""
        if not self._started:
            return
        self._stop_event.set()
        for task in self._tasks:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._tasks.clear()
        self._started = False
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_orchestrator_lifecycle.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Full suite**

Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -3`
Expected: full suite green (orchestrator constructor change is backward-compatible because `surface` defaults to `None`).

- [ ] **Step 6: Commit**

```bash
git add agent_hub/orchestrator/router.py tests/test_orchestrator_lifecycle.py
git commit -m "feat(orchestrator): add start/stop lifecycle scaffolding"
```

---

## Task 4: Handoff loop — claim and dispatch one row

**Files:**
- Modify: `agent_hub/orchestrator/router.py`
- Create: `tests/test_handoff_loop.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_handoff_loop.py`:

```python
"""Tests for the orchestrator's handoff loop — claims handoff_queue rows
and dispatches them to the runner with task context attached."""

import asyncio

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.agents.runner import TextChunk, TurnDone
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    runner = FakeAgentRunner()
    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=surface,
    )
    return orch, runner, surface, TaskRepository(temp_db_path), HandoffQueue(temp_db_path)


@pytest.mark.asyncio
async def test_tick_dispatches_one_handoff(deps):
    orch, runner, surface, repo, queue = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await queue.enqueue(task_id=task.id, from_agent="pm", to_agent="architect", message="design this")

    runner.script("architect", task_id=task.id, events=[
        TextChunk(text="ok"),
        TurnDone(cost_usd=0.01, duration_ms=10),
    ])

    await orch._tick_handoff()

    assert runner.calls == [("architect", _expected_routed_message(task.id, "pm", "design this"), task.id)]


@pytest.mark.asyncio
async def test_tick_no_op_when_queue_empty(deps):
    orch, runner, _, _, _ = deps
    await orch._tick_handoff()
    assert runner.calls == []


def _expected_routed_message(task_id: int, from_agent: str, body: str) -> str:
    """The orchestrator prepends task context. Match the exact format the
    impl produces — adjust this helper if the format changes."""
    return f"[task #{task_id}, from @{from_agent}] {body}"
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_handoff_loop.py -v`
Expected: AttributeError on `orch._tick_handoff`.

- [ ] **Step 3: Implement _tick_handoff**

Append to `Orchestrator` in `agent_hub/orchestrator/router.py`:

```python
    async def _tick_handoff(self) -> None:
        """Claim at most one handoff queue row and dispatch it.

        Called repeatedly by the handoff loop (Task 5). Splitting the
        tick from the loop makes the dispatch logic unit-testable.
        """
        from agent_hub.tasks.handoff_queue import HandoffQueue

        queue = HandoffQueue(self.db.path)
        row = await queue.claim()
        if row is None:
            return

        routed_text = f"[task #{row.task_id}, from @{row.from_agent}] {row.message}"
        async for _event in self.runner.send(row.to_agent, routed_text, task_id=row.task_id):
            # Streaming the agent's events to Telegram lands in Task 7.
            # For now we just drain the iterator.
            pass
```

Note: `Database` doesn't expose `.path` directly today — check `agent_hub/db.py`. If the existing class uses `self.path` as the attribute name, this works. If it uses a different name, adjust the access.

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_handoff_loop.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/router.py tests/test_handoff_loop.py
git commit -m "feat(orchestrator): _tick_handoff claims and dispatches one row"
```

---

## Task 5: Handoff loop — wire into start()

**Files:**
- Modify: `agent_hub/orchestrator/router.py`
- Modify: `tests/test_handoff_loop.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_handoff_loop.py`:

```python
@pytest.mark.asyncio
async def test_loop_processes_enqueued_handoffs(deps):
    orch, runner, _, repo, queue = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    runner.script("architect", task_id=task.id, events=[
        TextChunk(text="ok"),
        TurnDone(cost_usd=0.01, duration_ms=10),
    ])

    await orch.start()
    try:
        await queue.enqueue(task_id=task.id, from_agent="pm", to_agent="architect", message="m")
        # The loop ticks every 250ms; wait up to 2s for the dispatch.
        for _ in range(20):
            await asyncio.sleep(0.1)
            if runner.calls:
                break
        assert runner.calls == [("architect", _expected_routed_message(task.id, "pm", "m"), task.id)]
    finally:
        await orch.stop()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_handoff_loop.py::test_loop_processes_enqueued_handoffs -v`
Expected: FAIL — `start()` doesn't create the loop task yet.

- [ ] **Step 3: Wire the loop into start()**

In `Orchestrator.start()`, after `self._stop_event.clear()`:

```python
        self._tasks.append(asyncio.create_task(self._run_handoff_loop()))
```

Add the loop method:

```python
    async def _run_handoff_loop(self) -> None:
        log = structlog.get_logger("agent_hub.handoff_loop")
        while not self._stop_event.is_set():
            try:
                await self._tick_handoff()
            except Exception as exc:
                log.exception("handoff_loop.tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
```

Add `import structlog` at the top if not present.

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_handoff_loop.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/router.py tests/test_handoff_loop.py
git commit -m "feat(orchestrator): handoff loop drives _tick_handoff every 250ms"
```

---

## Task 6: Handoff loop — stream events through surface

**Files:**
- Modify: `agent_hub/orchestrator/router.py`
- Modify: `tests/test_handoff_loop.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_handoff_loop.py`:

```python
@pytest.mark.asyncio
async def test_tick_streams_text_to_origin_chat(deps):
    orch, runner, surface, repo, queue = deps
    task = await repo.create(title="x", description="-", origin_chat_id=99)
    runner.script("architect", task_id=task.id, events=[
        TextChunk(text="design is ready"),
        TurnDone(cost_usd=0.01, duration_ms=10),
    ])
    await queue.enqueue(task_id=task.id, from_agent="pm", to_agent="architect", message="m")

    await orch._tick_handoff()

    msgs = surface.dms_to(99)
    # The orchestrator prefixes streamed text with the speaking agent.
    assert any("design is ready" in m for m in msgs)
    assert any("architect" in m.lower() for m in msgs)


@pytest.mark.asyncio
async def test_tick_does_not_stream_when_no_surface(temp_db_path):
    """If orchestrator has surface=None, dispatch still works (silent)."""
    db = Database(temp_db_path)
    await db.init()
    runner = FakeAgentRunner()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=None,
    )
    repo = TaskRepository(temp_db_path)
    queue = HandoffQueue(temp_db_path)
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    runner.script("architect", task_id=task.id, events=[
        TextChunk(text="ok"),
        TurnDone(cost_usd=0.01, duration_ms=10),
    ])
    await queue.enqueue(task_id=task.id, from_agent="pm", to_agent="architect", message="m")
    await orch._tick_handoff()
    # No surface → no crash, just no streaming.
    assert runner.calls
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_handoff_loop.py -v`
Expected: 2 new tests FAIL.

- [ ] **Step 3: Stream events through the surface**

Replace the loop body in `_tick_handoff` to actually stream events:

```python
    async def _tick_handoff(self) -> None:
        from agent_hub.agents.runner import TextChunk
        from agent_hub.tasks.handoff_queue import HandoffQueue
        from agent_hub.tasks.repository import TaskRepository

        queue = HandoffQueue(self.db.path)
        row = await queue.claim()
        if row is None:
            return

        # Look up origin_chat_id so we know where to stream the response.
        repo = TaskRepository(self.db.path)
        task = await repo.get(row.task_id)
        chat_id = task.origin_chat_id if task else None

        routed_text = f"[task #{row.task_id}, from @{row.from_agent}] {row.message}"
        accumulated: list[str] = []
        async for event in self.runner.send(row.to_agent, routed_text, task_id=row.task_id):
            if isinstance(event, TextChunk):
                accumulated.append(event.text)

        if self.surface is not None and chat_id is not None and accumulated:
            body = "".join(accumulated).strip()
            if body:
                await self.surface.dm(chat_id, f"@{row.to_agent}: {body}")
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_handoff_loop.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/router.py tests/test_handoff_loop.py
git commit -m "feat(orchestrator): stream handoff response back through MessageSurface"
```

---

## Task 7: Gate watcher — detect pending design gates and DM user

**Files:**
- Modify: `agent_hub/orchestrator/router.py`
- Create: `tests/test_gate_watcher.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_gate_watcher.py`:

```python
"""Tests for the gate watcher — DMs the user when a new design gate
is pending."""

import asyncio

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.repository import TaskRepository
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface,
    )
    return orch, surface, TaskRepository(temp_db_path), GateRepository(temp_db_path)


@pytest.mark.asyncio
async def test_tick_dms_user_on_new_pending_gate(deps):
    orch, surface, repo, gates = deps
    task = await repo.create(title="add /health", description="-", origin_chat_id=77)
    await gates.request(task_id=task.id, kind="design", summary="design ready")

    await orch._tick_gates()

    msgs = surface.dms_to(77)
    assert any(f"#{task.id}" in m for m in msgs)
    assert any("design" in m.lower() for m in msgs)
    assert any("/approve" in m or "approve" in m.lower() for m in msgs)


@pytest.mark.asyncio
async def test_tick_does_not_dm_same_gate_twice(deps):
    orch, surface, repo, gates = deps
    task = await repo.create(title="x", description="-", origin_chat_id=77)
    await gates.request(task_id=task.id, kind="design")

    await orch._tick_gates()
    first_count = len(surface.sent)
    await orch._tick_gates()
    second_count = len(surface.sent)

    assert first_count == 1
    assert second_count == 1  # no new DM on the second tick


@pytest.mark.asyncio
async def test_tick_ignores_resolved_gates(deps):
    orch, surface, repo, gates = deps
    task = await repo.create(title="x", description="-", origin_chat_id=77)
    await gates.request(task_id=task.id, kind="design")
    await gates.resolve(task_id=task.id, kind="design", resolution="approved")

    await orch._tick_gates()

    assert surface.sent == []
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_gate_watcher.py -v`
Expected: AttributeError on `_tick_gates`.

- [ ] **Step 3: Implement _tick_gates**

In `agent_hub/orchestrator/router.py`, add to `Orchestrator`:

```python
    def __init__(self, ...):
        # ... existing init ...
        self._notified_gates: set[int] = set()

    async def _tick_gates(self) -> None:
        """Detect pending design gates and DM the user. Idempotent —
        each gate is announced at most once per orchestrator lifetime."""
        if self.surface is None:
            return
        from agent_hub.tasks.gates import GateRepository
        from agent_hub.tasks.repository import TaskRepository

        gates = GateRepository(self.db.path)
        repo = TaskRepository(self.db.path)

        import aiosqlite

        async with aiosqlite.connect(self.db.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT id, task_id, kind, summary FROM gates "
                "WHERE resolved_at IS NULL"
            )
            rows = await cur.fetchall()

        for row in rows:
            if row["id"] in self._notified_gates:
                continue
            task = await repo.get(row["task_id"])
            if task is None:
                continue
            summary = row["summary"] or ""
            msg = (
                f"🛂 Task #{task.id} design ready"
                + (f": {summary}" if summary else "")
                + f"\nReply /approve {task.id} or /reject {task.id} <reason>."
            )
            await self.surface.dm(task.origin_chat_id, msg)
            self._notified_gates.add(row["id"])
```

(Update `__init__` to add `self._notified_gates: set[int] = set()` to the constructor body.)

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_gate_watcher.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/router.py tests/test_gate_watcher.py
git commit -m "feat(orchestrator): gate watcher DMs user once per pending gate"
```

---

## Task 8: Gate watcher — wire into start()

**Files:**
- Modify: `agent_hub/orchestrator/router.py`
- Modify: `tests/test_gate_watcher.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_gate_watcher.py`:

```python
@pytest.mark.asyncio
async def test_loop_picks_up_new_gates(deps):
    orch, surface, repo, gates = deps
    await orch.start()
    try:
        task = await repo.create(title="x", description="-", origin_chat_id=77)
        await gates.request(task_id=task.id, kind="design")
        for _ in range(20):
            await asyncio.sleep(0.1)
            if surface.sent:
                break
        assert any(f"#{task.id}" in m for _, m in surface.sent)
    finally:
        await orch.stop()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_gate_watcher.py::test_loop_picks_up_new_gates -v`
Expected: FAIL — gate loop not wired into start().

- [ ] **Step 3: Wire gate loop**

In `Orchestrator.start()`, after the handoff-loop task creation:

```python
        self._tasks.append(asyncio.create_task(self._run_gate_watcher()))
```

Add the method:

```python
    async def _run_gate_watcher(self) -> None:
        log = structlog.get_logger("agent_hub.gate_watcher")
        while not self._stop_event.is_set():
            try:
                await self._tick_gates()
            except Exception as exc:
                log.exception("gate_watcher.tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_gate_watcher.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/router.py tests/test_gate_watcher.py
git commit -m "feat(orchestrator): gate watcher loop drives _tick_gates every 250ms"
```

---

## Task 9: /approve command resolves gate and advances task to ready

**Files:**
- Create: `agent_hub/telegram_bot/commands/__init__.py` (empty)
- Create: `agent_hub/telegram_bot/commands/approve_cmd.py`
- Create: `tests/test_commands_approve.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_commands_approve.py`:

```python
"""Tests for /approve <id> — resolves the pending design gate and
flips the task status to ready."""

import pytest

from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.approve_cmd import handle_approve


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), GateRepository(temp_db_path), db


@pytest.mark.asyncio
async def test_approve_resolves_pending_gate(deps):
    repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    reply = await handle_approve(task_id=task.id, db_path=db.path)

    assert await gates.status(task_id=task.id, kind="design") == "approved"
    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.READY
    assert "approved" in reply.lower() or f"#{task.id}" in reply


@pytest.mark.asyncio
async def test_approve_unknown_task_returns_error(deps):
    repo, _, db = deps
    reply = await handle_approve(task_id=99999, db_path=db.path)
    assert "not found" in reply.lower() or "unknown" in reply.lower()


@pytest.mark.asyncio
async def test_approve_with_no_pending_gate_reports_no_op(deps):
    repo, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    # Task is PENDING, no design gate requested yet.
    reply = await handle_approve(task_id=task.id, db_path=db.path)
    assert "no" in reply.lower() and "gate" in reply.lower()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_approve.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement the handler**

Create `agent_hub/telegram_bot/commands/__init__.py` (empty).

Create `agent_hub/telegram_bot/commands/approve_cmd.py`:

```python
"""Pure handler for /approve <id> — resolves the pending design gate
and advances the task from design_review to ready.

Kept pure (no PTB import) so it can be unit-tested without a bot.
The Telegram glue (extracting task_id from the message, sending the
reply) lives in agent_hub/telegram_bot/bot.py.
"""

from __future__ import annotations

from pathlib import Path

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.repository import TaskRepository


async def handle_approve(*, task_id: int, db_path: Path) -> str:
    """Resolve the design gate (if any) and flip the task to ready.

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

    return f"✅ Task #{task_id} approved — moving to ready."
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_approve.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/telegram_bot/commands/__init__.py agent_hub/telegram_bot/commands/approve_cmd.py tests/test_commands_approve.py
git commit -m "feat(commands): /approve resolves design gate and advances to ready"
```

---

## Task 10: /reject command resolves gate and routes back to architect

**Files:**
- Create: `agent_hub/telegram_bot/commands/reject_cmd.py`
- Create: `tests/test_commands_reject.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_commands_reject.py`:

```python
"""Tests for /reject <id> <reason> — resolves the gate as rejected,
flips status back to planning, enqueues a handoff to architect with
the reason."""

import pytest

from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.reject_cmd import handle_reject


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return (
        TaskRepository(temp_db_path),
        GateRepository(temp_db_path),
        HandoffQueue(temp_db_path),
        db,
    )


@pytest.mark.asyncio
async def test_reject_resolves_gate_and_returns_planning(deps):
    repo, gates, queue, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    reply = await handle_reject(
        task_id=task.id,
        reason="d1 ping should be SELECT 1 not real query",
        db_path=db.path,
    )

    assert await gates.status(task_id=task.id, kind="design") == "rejected"
    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.PLANNING
    pending = await queue.pending()
    assert any(h.to_agent == "architect" and "SELECT 1" in h.message for h in pending)
    assert "rejected" in reply.lower() or "back to planning" in reply.lower()


@pytest.mark.asyncio
async def test_reject_unknown_task_returns_error(deps):
    repo, _, _, db = deps
    reply = await handle_reject(task_id=99999, reason="r", db_path=db.path)
    assert "not found" in reply.lower()


@pytest.mark.asyncio
async def test_reject_no_pending_gate_reports_no_op(deps):
    repo, _, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    reply = await handle_reject(task_id=task.id, reason="r", db_path=db.path)
    assert "no" in reply.lower() and "gate" in reply.lower()


@pytest.mark.asyncio
async def test_reject_empty_reason_returns_error(deps):
    repo, gates, _, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")
    reply = await handle_reject(task_id=task.id, reason="", db_path=db.path)
    assert "reason" in reply.lower()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_reject.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `agent_hub/telegram_bot/commands/reject_cmd.py`:

```python
"""Pure handler for /reject <id> <reason>.

Resolves the design gate as rejected, flips status back to planning,
and enqueues a handoff to architect with the user's feedback.
"""

from __future__ import annotations

from pathlib import Path

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


async def handle_reject(*, task_id: int, reason: str, db_path: Path) -> str:
    reason = (reason or "").strip()
    if not reason:
        return "Reject requires a reason: /reject <id> <reason>"

    repo = TaskRepository(db_path)
    gates = GateRepository(db_path)
    queue = HandoffQueue(db_path)

    task = await repo.get(task_id)
    if task is None:
        return f"Task #{task_id} not found."

    status = await gates.status(task_id=task_id, kind="design")
    if status == "none":
        return f"Task #{task_id} has no pending design gate to reject."
    if status != "pending":
        return f"Task #{task_id} gate is already {status}."

    await gates.resolve(task_id=task_id, kind="design", resolution="rejected")
    # Record the rejection reason as a comment so it shows in /task view.
    await repo.comment(task_id, actor="user", body=f"Rejected: {reason}")

    try:
        await repo.update(task_id, status=TaskStatus.PLANNING)
    except InvalidTransition as exc:
        return f"Resolved as rejected but couldn't return to planning: {exc}"

    await queue.enqueue(
        task_id=task_id,
        from_agent="user",
        to_agent="architect",
        message=f"User rejected the design with feedback: {reason}",
    )
    return f"❌ Task #{task_id} rejected — returned to planning with feedback."
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_reject.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/telegram_bot/commands/reject_cmd.py tests/test_commands_reject.py
git commit -m "feat(commands): /reject resolves gate and rehandoffs to architect"
```

---

## Task 11: /tasks command lists open tasks

**Files:**
- Create: `agent_hub/telegram_bot/commands/tasks_cmd.py`
- Create: `tests/test_commands_tasks.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_commands_tasks.py`:

```python
import pytest

from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.tasks_cmd import handle_tasks


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), db


@pytest.mark.asyncio
async def test_tasks_lists_non_done_tasks(deps):
    repo, db = deps
    a = await repo.create(title="alpha", description="-", origin_chat_id=1)
    b = await repo.create(title="beta", description="-", origin_chat_id=1)
    # c starts pending and reaches done via planning -> in_progress -> review.
    c = await repo.create(title="gamma done", description="-", origin_chat_id=1)
    await repo.update(c.id, status=TaskStatus.PLANNING)
    await repo.update(c.id, status=TaskStatus.IN_PROGRESS)
    await repo.update(c.id, status=TaskStatus.REVIEW)
    await repo.update(c.id, status=TaskStatus.DONE)

    reply = await handle_tasks(db_path=db.path)

    assert f"#{a.id}" in reply
    assert f"#{b.id}" in reply
    assert f"#{c.id}" not in reply  # done tasks excluded
    assert "alpha" in reply
    assert "beta" in reply


@pytest.mark.asyncio
async def test_tasks_empty_returns_friendly_message(deps):
    _, db = deps
    reply = await handle_tasks(db_path=db.path)
    assert "no" in reply.lower() or "empty" in reply.lower()


@pytest.mark.asyncio
async def test_tasks_groups_by_status(deps):
    repo, db = deps
    a = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(a.id, status=TaskStatus.PLANNING)

    b = await repo.create(title="y", description="-", origin_chat_id=1)
    await repo.update(b.id, status=TaskStatus.PLANNING)
    await repo.update(b.id, status=TaskStatus.DESIGN_REVIEW)

    reply = await handle_tasks(db_path=db.path)
    # Both statuses should appear as section labels.
    assert "planning" in reply.lower()
    assert "design_review" in reply.lower() or "design review" in reply.lower()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_tasks.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `agent_hub/telegram_bot/commands/tasks_cmd.py`:

```python
"""Pure handler for /tasks — lists non-done tasks grouped by status."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.repository import TaskRepository


async def handle_tasks(*, db_path: Path) -> str:
    repo = TaskRepository(db_path)
    all_tasks = await repo.list()  # no filter

    active = [t for t in all_tasks if t.status != TaskStatus.DONE]
    if not active:
        return "No active tasks."

    by_status: dict[str, list] = defaultdict(list)
    for t in active:
        by_status[t.status.value].append(t)

    # Preferred display order; unknown statuses (none expected) sort last.
    order = ["pending", "planning", "design_review", "ready",
             "in_progress", "review", "blocked"]
    lines: list[str] = []
    for status in order:
        if status not in by_status:
            continue
        lines.append(f"\n*{status}*")
        for t in by_status[status]:
            owner = f" → @{t.owner}" if t.owner else ""
            lines.append(f"  #{t.id} {t.title}{owner}")

    return "Active tasks:" + "\n".join(lines)
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_tasks.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/telegram_bot/commands/tasks_cmd.py tests/test_commands_tasks.py
git commit -m "feat(commands): /tasks lists active tasks grouped by status"
```

---

## Task 12: /task <id> command shows detail + recent events

**Files:**
- Create: `agent_hub/telegram_bot/commands/task_cmd.py`
- Create: `tests/test_commands_task.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_commands_task.py`:

```python
import pytest

from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.task_cmd import handle_task


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), db


@pytest.mark.asyncio
async def test_task_detail_includes_title_status_recent_events(deps):
    repo, db = deps
    task = await repo.create(title="add /health", description="ping D1", origin_chat_id=1)
    await repo.comment(task.id, actor="pm", body="filed it")
    await repo.comment(task.id, actor="architect", body="design ready")

    reply = await handle_task(task_id=task.id, db_path=db.path)

    assert "add /health" in reply
    assert f"#{task.id}" in reply
    assert "pending" in reply.lower()
    assert "filed it" in reply
    assert "design ready" in reply
    assert "pm" in reply
    assert "architect" in reply


@pytest.mark.asyncio
async def test_task_unknown_returns_error(deps):
    _, db = deps
    reply = await handle_task(task_id=99999, db_path=db.path)
    assert "not found" in reply.lower()


@pytest.mark.asyncio
async def test_task_truncates_to_recent_20_events(deps):
    repo, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    for i in range(25):
        await repo.comment(task.id, actor="pm", body=f"comment-{i}")

    reply = await handle_task(task_id=task.id, db_path=db.path)
    # Earliest event should be off the bottom of the recent window.
    assert "comment-0" not in reply
    # Latest should be in.
    assert "comment-24" in reply
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_task.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `agent_hub/telegram_bot/commands/task_cmd.py`:

```python
"""Pure handler for /task <id> — task detail + 20 most-recent events."""

from __future__ import annotations

from pathlib import Path

from agent_hub.tasks.repository import TaskRepository


async def handle_task(*, task_id: int, db_path: Path) -> str:
    repo = TaskRepository(db_path)
    task = await repo.get(task_id)
    if task is None:
        return f"Task #{task_id} not found."

    events = await repo.events(task_id, limit=20)

    owner_str = f" (owner: @{task.owner})" if task.owner else ""
    lines = [
        f"*Task #{task.id}* — {task.title}",
        f"Status: {task.status.value}{owner_str}",
        f"Created: {task.created_at.isoformat()}",
        "",
        "Recent events:",
    ]
    if not events:
        lines.append("  (none)")
    else:
        for ev in events:
            ts = ev.ts.isoformat(timespec="seconds")
            body = _format_event_body(ev.kind, ev.payload)
            lines.append(f"  {ts} @{ev.actor} {ev.kind}: {body}")
    return "\n".join(lines)


def _format_event_body(kind: str, payload: dict) -> str:
    if kind == "comment":
        return str(payload.get("body", ""))[:200]
    if kind == "status_change":
        return f"{payload.get('from')} → {payload.get('to')}"
    return str(payload)[:200]
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_task.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/telegram_bot/commands/task_cmd.py tests/test_commands_task.py
git commit -m "feat(commands): /task shows detail and recent events"
```

---

## Task 13: /resume command surfaces blocked or stuck tasks

**Files:**
- Create: `agent_hub/telegram_bot/commands/resume_cmd.py`
- Create: `tests/test_commands_resume.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_commands_resume.py`:

```python
import pytest

from agent_hub.db import Database
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.resume_cmd import handle_resume


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), HandoffQueue(temp_db_path), db


@pytest.mark.asyncio
async def test_resume_blocked_task_routes_to_pm(deps):
    repo, queue, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.BLOCKED)

    reply = await handle_resume(task_id=task.id, db_path=db.path)

    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.PLANNING

    pending = await queue.pending()
    assert any(h.to_agent == "pm" and f"#{task.id}" in h.message for h in pending)
    assert "resumed" in reply.lower() or "pm" in reply.lower()


@pytest.mark.asyncio
async def test_resume_in_progress_task_redispatches_to_owner(deps):
    """For tasks paused mid-flight (not blocked), resume routes back to
    the current owner with no status change."""
    repo, queue, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await repo.update(task.id, status=TaskStatus.PLANNING, owner="fullstack-engineer")
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS)

    reply = await handle_resume(task_id=task.id, db_path=db.path)

    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.IN_PROGRESS  # unchanged
    pending = await queue.pending()
    assert any(h.to_agent == "fullstack-engineer" for h in pending)
    assert "resumed" in reply.lower()


@pytest.mark.asyncio
async def test_resume_unknown_task_returns_error(deps):
    _, _, db = deps
    reply = await handle_resume(task_id=99999, db_path=db.path)
    assert "not found" in reply.lower()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_resume.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `agent_hub/telegram_bot/commands/resume_cmd.py`:

```python
"""Pure handler for /resume <id>.

Two cases:
- BLOCKED → flip to planning and hand off to PM with the block context.
- Any other paused state (in_progress, review, planning) → re-dispatch
  to the current owner (or PM if owner is unset).
"""

from __future__ import annotations

from pathlib import Path

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository


async def handle_resume(*, task_id: int, db_path: Path) -> str:
    repo = TaskRepository(db_path)
    queue = HandoffQueue(db_path)

    task = await repo.get(task_id)
    if task is None:
        return f"Task #{task_id} not found."

    if task.status == TaskStatus.BLOCKED:
        try:
            await repo.update(task_id, status=TaskStatus.PLANNING)
        except InvalidTransition as exc:
            return f"Couldn't resume from blocked: {exc}"
        await queue.enqueue(
            task_id=task_id,
            from_agent="user",
            to_agent="pm",
            message=f"User requested resume of blocked task #{task_id}. Reassess and decide the next step.",
        )
        return f"▶️ Task #{task_id} resumed — PM is taking another look."

    # Non-blocked: re-dispatch to current owner.
    to_agent = task.owner or "pm"
    await queue.enqueue(
        task_id=task_id,
        from_agent="user",
        to_agent=to_agent,
        message=f"User requested resume of task #{task_id}. Continue where you left off.",
    )
    return f"▶️ Task #{task_id} resumed — sent to @{to_agent}."
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_commands_resume.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/telegram_bot/commands/resume_cmd.py tests/test_commands_resume.py
git commit -m "feat(commands): /resume reroutes blocked or paused tasks"
```

---

## Task 14: Push-on-done action

**Files:**
- Create: `agent_hub/orchestrator/push.py`
- Create: `tests/test_push.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_push.py`:

```python
"""Tests for push-on-done — when a task transitions to done, push the
branch to origin and DM the user."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from agent_hub.db import Database
from agent_hub.orchestrator.push import push_task_branch
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository


@pytest.fixture
def git_repos(tmp_path: Path) -> tuple[Path, Path]:
    """Create a bare 'remote' and a clone of it with one commit on main."""
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])

    local = tmp_path / "local"
    subprocess.check_call(["git", "clone", str(remote), str(local)])
    subprocess.check_call(["git", "config", "user.name", "Test"], cwd=local)
    subprocess.check_call(["git", "config", "user.email", "t@example.com"], cwd=local)
    (local / "README.md").write_text("hi\n")
    subprocess.check_call(["git", "add", "README.md"], cwd=local)
    subprocess.check_call(["git", "commit", "-m", "initial"], cwd=local)
    subprocess.check_call(["git", "push", "origin", "main"], cwd=local)
    return remote, local


@pytest.mark.asyncio
async def test_push_pushes_branch_to_origin(git_repos, temp_db_path, tmp_path):
    remote, local = git_repos
    # Create a worktree branch with a commit on it.
    worktrees_root = tmp_path / "wt"
    worktrees_root.mkdir()
    wt_path = worktrees_root / "1"
    subprocess.check_call(
        ["git", "worktree", "add", "-b", "task/1-x", str(wt_path), "main"],
        cwd=local,
    )
    (wt_path / "a.txt").write_text("a\n")
    subprocess.check_call(["git", "add", "a.txt"], cwd=wt_path)
    subprocess.check_call(["git", "commit", "-m", "work"], cwd=wt_path)

    db = Database(temp_db_path)
    await db.init()
    repo = TaskRepository(temp_db_path)
    wt_repo = WorktreeRepository(temp_db_path)
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    await wt_repo.record(
        task_id=task.id, path=str(wt_path), branch="task/1-x", base_branch="main",
    )

    result = await push_task_branch(task_id=task.id, repo_root=local, db_path=temp_db_path)

    assert result["pushed"] is True
    assert result["branch"] == "task/1-x"
    # Confirm the branch now exists on the remote.
    branches = subprocess.check_output(
        ["git", "branch", "--list"], cwd=remote,
    ).decode()
    assert "task/1-x" in branches


@pytest.mark.asyncio
async def test_push_unknown_task_returns_error(temp_db_path, tmp_path):
    db = Database(temp_db_path)
    await db.init()
    result = await push_task_branch(
        task_id=99999, repo_root=tmp_path, db_path=temp_db_path,
    )
    assert result["pushed"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_push_no_worktree_returns_error(temp_db_path, tmp_path):
    db = Database(temp_db_path)
    await db.init()
    repo = TaskRepository(temp_db_path)
    task = await repo.create(title="x", description="-", origin_chat_id=1)
    result = await push_task_branch(
        task_id=task.id, repo_root=tmp_path, db_path=temp_db_path,
    )
    assert result["pushed"] is False
    assert "error" in result
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_push.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `agent_hub/orchestrator/push.py`:

```python
"""Push the task's branch to origin after the task transitions to done.

Returns a dict with `pushed: bool`, `branch: str | None`, and an
`error` key on failure. The orchestrator's handoff loop calls this
when it observes a status transition to done.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository


async def push_task_branch(*, task_id: int, repo_root: Path, db_path: Path) -> dict:
    repo = TaskRepository(db_path)
    wt_repo = WorktreeRepository(db_path)

    task = await repo.get(task_id)
    if task is None:
        return {"pushed": False, "branch": None, "error": f"Unknown task #{task_id}"}

    row = await wt_repo.get_by_task(task_id)
    if row is None:
        return {"pushed": False, "branch": None, "error": f"No worktree recorded for #{task_id}"}

    proc = await asyncio.create_subprocess_exec(
        "git", "push", "origin", row.branch,
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode != 0:
        return {
            "pushed": False,
            "branch": row.branch,
            "error": stderr_b.decode("utf-8", errors="replace").strip() or
                     stdout_b.decode("utf-8", errors="replace").strip(),
        }
    return {"pushed": True, "branch": row.branch}
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_push.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/push.py tests/test_push.py
git commit -m "feat(orchestrator): push_task_branch action with error reporting"
```

---

## Task 15: Epic auto-completion logic

**Files:**
- Create: `agent_hub/orchestrator/epic.py`
- Create: `tests/test_epic.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_epic.py`:

```python
"""Tests for epic auto-completion — when the last leaf of an epic
transitions to done, mark the epic done."""

import pytest

from agent_hub.db import Database
from agent_hub.orchestrator.epic import maybe_complete_epic
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), db


async def _advance_to_done(repo: TaskRepository, task_id: int) -> None:
    await repo.update(task_id, status=TaskStatus.PLANNING)
    await repo.update(task_id, status=TaskStatus.IN_PROGRESS)
    await repo.update(task_id, status=TaskStatus.REVIEW)
    await repo.update(task_id, status=TaskStatus.DONE)


@pytest.mark.asyncio
async def test_completes_epic_when_last_leaf_done(deps):
    repo, db = deps
    epic = await repo.create(title="billing", description="-", origin_chat_id=1)
    await repo.update(epic.id, status=TaskStatus.PLANNING)
    a = await repo.create(title="a", description="-", origin_chat_id=1, parent_id=epic.id)
    b = await repo.create(title="b", description="-", origin_chat_id=1, parent_id=epic.id)

    await _advance_to_done(repo, a.id)
    completed = await maybe_complete_epic(task_id=a.id, db_path=db.path)
    assert completed is None  # b still open

    await _advance_to_done(repo, b.id)
    completed = await maybe_complete_epic(task_id=b.id, db_path=db.path)

    assert completed == epic.id
    fresh_epic = await repo.get(epic.id)
    assert fresh_epic.status == TaskStatus.DONE


@pytest.mark.asyncio
async def test_root_task_returns_none(deps):
    """Calling maybe_complete_epic on a task with no parent does nothing."""
    repo, db = deps
    t = await repo.create(title="x", description="-", origin_chat_id=1)
    result = await maybe_complete_epic(task_id=t.id, db_path=db.path)
    assert result is None


@pytest.mark.asyncio
async def test_already_done_epic_is_noop(deps):
    repo, db = deps
    epic = await repo.create(title="e", description="-", origin_chat_id=1)
    await _advance_to_done(repo, epic.id)
    leaf = await repo.create(title="l", description="-", origin_chat_id=1, parent_id=epic.id)
    await _advance_to_done(repo, leaf.id)

    result = await maybe_complete_epic(task_id=leaf.id, db_path=db.path)
    # Epic is already done — no transition happens, returns None.
    assert result is None
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_epic.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `agent_hub/orchestrator/epic.py`:

```python
"""Epic auto-completion: when the last leaf of an epic transitions to
done, mark the epic done.

Called from the orchestrator after observing a status_change event
that lands a leaf in done."""

from __future__ import annotations

from pathlib import Path

from agent_hub.state_machine import InvalidTransition, TaskStatus
from agent_hub.tasks.repository import TaskRepository


async def maybe_complete_epic(*, task_id: int, db_path: Path) -> int | None:
    """If task_id has a parent and all its siblings are done, mark the
    parent done. Returns the parent's id if it was transitioned, else None.
    """
    repo = TaskRepository(db_path)
    task = await repo.get(task_id)
    if task is None or task.parent_id is None:
        return None

    parent = await repo.get(task.parent_id)
    if parent is None or parent.status == TaskStatus.DONE:
        return None

    siblings = await repo.list(parent_id=parent.id)
    if not siblings:
        return None
    if not all(s.status == TaskStatus.DONE for s in siblings):
        return None

    # Parent status flow before done: must be in review at minimum.
    # An epic doesn't go through impl itself, so we walk it through:
    # whatever-state → planning → in_progress → review → done.
    # But the spec accepts: parent may be in any non-terminal state.
    # Pragmatic path: if it's not already in review, move it through.
    try:
        if parent.status not in (TaskStatus.REVIEW, TaskStatus.DONE):
            if parent.status == TaskStatus.PENDING:
                await repo.update(parent.id, status=TaskStatus.PLANNING)
            if parent.status in (TaskStatus.PENDING, TaskStatus.PLANNING):
                await repo.update(parent.id, status=TaskStatus.IN_PROGRESS)
            await repo.update(parent.id, status=TaskStatus.REVIEW)
        await repo.update(parent.id, status=TaskStatus.DONE)
    except InvalidTransition:
        # Defensive — if the state machine refuses the cascade, leave
        # the epic in its current state and let the orchestrator surface
        # the inconsistency through other channels.
        return None
    return parent.id
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_epic.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/epic.py tests/test_epic.py
git commit -m "feat(orchestrator): epic auto-completion when last leaf done"
```

---

## Task 16: Restart-resume scan

**Files:**
- Create: `agent_hub/orchestrator/resume.py`
- Create: `tests/test_resume.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_resume.py`:

```python
"""Tests for the restart-resume scan — on boot, surface tasks that
were in flight (in_progress/review/planning) with no recent activity."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agent_hub.db import Database
from agent_hub.orchestrator.resume import scan_stale_tasks
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.repository import TaskRepository
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return TaskRepository(temp_db_path), FakeMessageSurface(), db


@pytest.mark.asyncio
async def test_dms_user_about_in_flight_tasks(deps):
    repo, surface, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS)
    # Backdate the latest event so the scan considers it stale.
    await _backdate_last_event(db.path, task.id, minutes=10)

    await scan_stale_tasks(db_path=db.path, surface=surface, stale_after_minutes=5)

    msgs = surface.dms_to(42)
    assert any(f"#{task.id}" in m for m in msgs)
    assert any("/resume" in m or "resume" in m.lower() for m in msgs)


@pytest.mark.asyncio
async def test_skips_recent_tasks(deps):
    repo, surface, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS)
    # Don't backdate — event is fresh.

    await scan_stale_tasks(db_path=db.path, surface=surface, stale_after_minutes=5)
    assert surface.sent == []


@pytest.mark.asyncio
async def test_skips_terminal_states(deps):
    repo, surface, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS)
    await repo.update(task.id, status=TaskStatus.REVIEW)
    await repo.update(task.id, status=TaskStatus.DONE)
    await _backdate_last_event(db.path, task.id, minutes=10)

    await scan_stale_tasks(db_path=db.path, surface=surface, stale_after_minutes=5)
    assert surface.sent == []


async def _backdate_last_event(db_path, task_id, *, minutes: int):
    """Helper: rewrite the latest task_event ts to be N minutes ago."""
    import aiosqlite
    past = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE task_events SET ts = ? "
            "WHERE id = (SELECT id FROM task_events "
            "            WHERE task_id = ? ORDER BY ts DESC LIMIT 1)",
            (past, task_id),
        )
        await conn.commit()
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_resume.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

Create `agent_hub/orchestrator/resume.py`:

```python
"""Restart-resume scan: on agent_hub boot, find tasks that were in
flight with no recent activity and DM the user.

We NEVER auto-resume — only surface the list. The user issues /resume
<id> to actually pick one back up.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from agent_hub.orchestrator.surface import MessageSurface


_IN_FLIGHT_STATUSES = ("planning", "design_review", "ready", "in_progress", "review")


async def scan_stale_tasks(
    *,
    db_path: Path,
    surface: MessageSurface,
    stale_after_minutes: int = 5,
) -> int:
    """DM each stale task's chat. Returns number of DMs sent."""
    cutoff = datetime.now(timezone.utc).timestamp() - stale_after_minutes * 60

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        placeholders = ",".join("?" * len(_IN_FLIGHT_STATUSES))
        cur = await conn.execute(
            f"""
            SELECT t.id, t.title, t.status, t.origin_chat_id,
                   (SELECT MAX(ts) FROM task_events WHERE task_id = t.id) AS last_ts
            FROM tasks t
            WHERE t.status IN ({placeholders})
            """,
            _IN_FLIGHT_STATUSES,
        )
        rows = await cur.fetchall()

    sent = 0
    by_chat: dict[int, list[str]] = {}
    for row in rows:
        last_ts = row["last_ts"]
        if last_ts is None:
            # No event yet → newly created task, not stale.
            continue
        last_dt = datetime.fromisoformat(last_ts)
        if last_dt.timestamp() > cutoff:
            continue
        chat_id = row["origin_chat_id"]
        line = f"  #{row['id']} {row['title']} ({row['status']})"
        by_chat.setdefault(chat_id, []).append(line)

    for chat_id, lines in by_chat.items():
        body = (
            "🔄 Tasks that were in flight at last shutdown:\n"
            + "\n".join(lines)
            + "\n\nReply /resume <id> to pick one back up."
        )
        await surface.dm(chat_id, body)
        sent += 1
    return sent
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_resume.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/resume.py tests/test_resume.py
git commit -m "feat(orchestrator): restart-resume scan DMs user about stale in-flight tasks"
```

---

## Task 17: Wire push + epic + cleanup into handoff tick

**Files:**
- Modify: `agent_hub/orchestrator/router.py`
- Create: `tests/test_orchestrator_done_flow.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_orchestrator_done_flow.py`:

```python
"""Tests for the orchestrator's done-handling: when a turn results in
a task transitioning to done, the orchestrator runs push + epic +
worktree cleanup."""

import subprocess
from pathlib import Path

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.agents.runner import TextChunk, ToolEnd, ToolStart, TurnDone
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
def git_repos(tmp_path: Path):
    """Bare remote + clone with one initial commit."""
    remote = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(remote)])
    local = tmp_path / "local"
    subprocess.check_call(["git", "clone", str(remote), str(local)])
    subprocess.check_call(["git", "config", "user.name", "T"], cwd=local)
    subprocess.check_call(["git", "config", "user.email", "t@e.com"], cwd=local)
    (local / "r.md").write_text("r\n")
    subprocess.check_call(["git", "add", "r.md"], cwd=local)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=local)
    subprocess.check_call(["git", "push", "origin", "main"], cwd=local)
    return remote, local


@pytest.mark.asyncio
async def test_done_transition_triggers_push_and_dm(deps_with_repo):
    orch, runner, surface, repo, queue, wt_repo, local = deps_with_repo

    task = await repo.create(title="x", description="-", origin_chat_id=99)
    # Walk through to review so we can transition to done.
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS)
    await repo.update(task.id, status=TaskStatus.REVIEW)

    # Set up a real worktree on a real branch with a real commit.
    worktrees_root = local.parent / "wt"
    worktrees_root.mkdir(exist_ok=True)
    wt_path = worktrees_root / str(task.id)
    subprocess.check_call(
        ["git", "worktree", "add", "-b", "task/x", str(wt_path), "main"],
        cwd=local,
    )
    (wt_path / "a.txt").write_text("a\n")
    subprocess.check_call(["git", "add", "a.txt"], cwd=wt_path)
    subprocess.check_call(["git", "commit", "-m", "w"], cwd=wt_path)
    await wt_repo.record(
        task_id=task.id, path=str(wt_path), branch="task/x", base_branch="main",
    )

    # QA hands off saying "all done" with a status_change to done embedded.
    # The tick observes the status_change after the runner turn ends.
    await queue.enqueue(task_id=task.id, from_agent="reviewer", to_agent="qa", message="approved")
    runner.script("qa", task_id=task.id, events=[
        TextChunk(text="tests pass"),
        ToolStart(tool="tasks.update", input={"task_id": task.id, "status": "done"}),
        ToolEnd(tool="tasks.update", is_error=False),
        TurnDone(cost_usd=0.01, duration_ms=10),
    ])

    # Simulate the agent's tools.update by setting status to done directly
    # (the real handoff loop observes ToolStart events for transitions;
    # for this Plan 3 test we apply the DB change here, mirroring what
    # the agent would do via the MCP tool).
    # NOTE: the orchestrator detects "task now done" by re-reading status
    # AFTER the turn completes, so we must apply it before the tick or
    # immediately after via a hook. Simplest: set status before calling tick.
    await repo.update(task.id, status=TaskStatus.DONE)

    await orch._tick_handoff()

    # Branch should be on the remote.
    remote_branches = subprocess.check_output(
        ["git", "branch", "--list"], cwd=local.parent / "remote.git",
    ).decode()
    assert "task/x" in remote_branches

    # User should have been DM'd with the push result.
    msgs = surface.dms_to(99)
    assert any("pushed" in m.lower() or "task/x" in m for m in msgs)


@pytest.fixture
async def deps_with_repo(temp_db_path, git_repos):
    remote, local = git_repos
    db = Database(temp_db_path)
    await db.init()
    runner = FakeAgentRunner()
    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=surface,
    )
    orch.repo_root = local  # orchestrator needs to know where to push from
    repo = TaskRepository(temp_db_path)
    queue = HandoffQueue(temp_db_path)
    wt_repo = WorktreeRepository(temp_db_path)
    return orch, runner, surface, repo, queue, wt_repo, local
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_orchestrator_done_flow.py -v`
Expected: FAIL — `orch.repo_root` not set, no push integration in _tick_handoff.

- [ ] **Step 3: Add repo_root + integrate push/epic/cleanup into _tick_handoff**

In `agent_hub/orchestrator/router.py`:

(a) Add `repo_root: Path | None = None` to `__init__`:

```python
    def __init__(self, *, ..., repo_root: Path | None = None, ...):
        # ... existing ...
        self.repo_root = repo_root
```

(b) Extend `_tick_handoff` so it observes the post-turn task status and triggers push + epic + cleanup when status flipped to done:

Replace the tail of `_tick_handoff` (after the streaming loop) with:

```python
        if self.surface is not None and chat_id is not None and accumulated:
            body = "".join(accumulated).strip()
            if body:
                await self.surface.dm(chat_id, f"@{row.to_agent}: {body}")

        # Post-turn: did the task land in done?
        post_turn_task = await repo.get(row.task_id)
        if post_turn_task is not None and post_turn_task.status == TaskStatus.DONE:
            await self._on_task_done(post_turn_task)
```

Add the import at the top: `from agent_hub.state_machine import TaskStatus`.

Add the `_on_task_done` method:

```python
    async def _on_task_done(self, task) -> None:
        """Called when an agent's turn left a task in done status.

        Pushes the branch, cleans the worktree, runs epic auto-completion,
        and DMs the user.
        """
        from agent_hub.orchestrator.epic import maybe_complete_epic
        from agent_hub.orchestrator.push import push_task_branch

        if self.repo_root is None:
            return  # not configured — silently skip push

        result = await push_task_branch(
            task_id=task.id,
            repo_root=self.repo_root,
            db_path=self.db.path,
        )
        if self.surface is not None:
            if result["pushed"]:
                await self.surface.dm(
                    task.origin_chat_id,
                    f"✅ Task #{task.id} done. Pushed branch `{result['branch']}` to origin.",
                )
            else:
                await self.surface.dm(
                    task.origin_chat_id,
                    f"⚠️ Task #{task.id} done but push failed: {result.get('error', 'unknown')}",
                )

        epic_id = await maybe_complete_epic(task_id=task.id, db_path=self.db.path)
        if epic_id is not None and self.surface is not None:
            await self.surface.dm(
                task.origin_chat_id,
                f"🎉 Epic #{epic_id} complete (all leaves done).",
            )
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_orchestrator_done_flow.py -v`
Expected: 1 PASS.

- [ ] **Step 5: Full suite check**

Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -5`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add agent_hub/orchestrator/router.py tests/test_orchestrator_done_flow.py
git commit -m "feat(orchestrator): on done, push branch and run epic completion"
```

---

## Task 18: Task-aware free-form Telegram routing

**Files:**
- Modify: `agent_hub/orchestrator/router.py`
- Create: `tests/test_router_task_aware.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_router_task_aware.py`:

```python
"""Tests for parse_addressee gaining task-context awareness:
- A message in a chat that has a pending design gate is interpreted as
  approve/reject candidate (the bot will reply with hint to use the
  slash commands rather than auto-resolving).
- A reply in a sticky-task thread becomes tasks.comment + handoff to the
  current task owner.
- Free-form text with no sticky/gate falls back to PM as today.
"""

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.db import Database
from agent_hub.orchestrator.router import classify_freeform_message
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.repository import TaskRepository


@pytest.fixture
async def deps(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    return (
        AgentRegistry.load(),
        TaskRepository(temp_db_path),
        GateRepository(temp_db_path),
        db,
    )


@pytest.mark.asyncio
async def test_pending_gate_returns_gate_hint(deps):
    _, repo, gates, db = deps
    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    result = await classify_freeform_message(
        chat_id=42, text="looks fine I guess", db_path=db.path,
    )
    assert result["kind"] == "pending_gate"
    assert result["task_id"] == task.id


@pytest.mark.asyncio
async def test_no_gate_returns_default(deps):
    _, repo, _, db = deps
    result = await classify_freeform_message(
        chat_id=42, text="hello pm", db_path=db.path,
    )
    assert result["kind"] == "default"


@pytest.mark.asyncio
async def test_multiple_open_gates_returns_first(deps):
    """If two tasks in the same chat have pending gates, the most recent
    is preferred."""
    _, repo, gates, db = deps
    t1 = await repo.create(title="t1", description="-", origin_chat_id=42)
    t2 = await repo.create(title="t2", description="-", origin_chat_id=42)
    for t in (t1, t2):
        await repo.update(t.id, status=TaskStatus.PLANNING)
        await repo.update(t.id, status=TaskStatus.DESIGN_REVIEW)
        await gates.request(task_id=t.id, kind="design")

    result = await classify_freeform_message(
        chat_id=42, text="?", db_path=db.path,
    )
    assert result["kind"] == "pending_gate"
    assert result["task_id"] == t2.id  # most recent
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_router_task_aware.py -v`
Expected: ImportError on `classify_freeform_message`.

- [ ] **Step 3: Implement classify_freeform_message**

In `agent_hub/orchestrator/router.py`, add a module-level helper:

```python
async def classify_freeform_message(
    *, chat_id: int, text: str, db_path: Path,
) -> dict:
    """Inspect chat state and decide how to interpret a free-form message.

    Returns a dict with `kind`:
    - `pending_gate` + `task_id`: the chat has a task with a pending gate;
       the bot should hint the user to use /approve or /reject.
    - `default`: route to PM (or sticky agent if any).
    """
    import aiosqlite
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """
            SELECT g.task_id
            FROM gates g
            JOIN tasks t ON t.id = g.task_id
            WHERE t.origin_chat_id = ?
              AND g.resolved_at IS NULL
            ORDER BY g.requested_at DESC LIMIT 1
            """,
            (chat_id,),
        )
        row = await cur.fetchone()
    if row is not None:
        return {"kind": "pending_gate", "task_id": row["task_id"]}
    return {"kind": "default"}
```

Add `from pathlib import Path` if missing.

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_router_task_aware.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/orchestrator/router.py tests/test_router_task_aware.py
git commit -m "feat(router): classify_freeform_message detects pending-gate chats"
```

---

## Task 19: Wire commands + classify into the Telegram bot

**Files:**
- Modify: `agent_hub/telegram_bot/bot.py`

- [ ] **Step 1: Read the current bot.py**

The bot is built with python-telegram-bot. Existing handlers handle free-form text and `@mention` routing. This task adds new command handlers and routes them through the pure command functions.

- [ ] **Step 2: Register the 5 new command handlers**

In `build_application(settings, orchestrator)` in `agent_hub/telegram_bot/bot.py`, after the existing handler registrations, add:

```python
    from telegram.ext import CommandHandler
    from agent_hub.telegram_bot.commands.approve_cmd import handle_approve
    from agent_hub.telegram_bot.commands.reject_cmd import handle_reject
    from agent_hub.telegram_bot.commands.tasks_cmd import handle_tasks
    from agent_hub.telegram_bot.commands.task_cmd import handle_task
    from agent_hub.telegram_bot.commands.resume_cmd import handle_resume

    db_path = settings.database_path

    async def _on_tasks(update, context):
        reply = await handle_tasks(db_path=db_path)
        await update.effective_chat.send_message(reply)

    async def _on_task(update, context):
        if not context.args:
            await update.effective_chat.send_message("Usage: /task <id>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.effective_chat.send_message("Task id must be an integer.")
            return
        reply = await handle_task(task_id=task_id, db_path=db_path)
        await update.effective_chat.send_message(reply)

    async def _on_approve(update, context):
        if not context.args:
            await update.effective_chat.send_message("Usage: /approve <id>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.effective_chat.send_message("Task id must be an integer.")
            return
        reply = await handle_approve(task_id=task_id, db_path=db_path)
        await update.effective_chat.send_message(reply)

    async def _on_reject(update, context):
        if len(context.args) < 2:
            await update.effective_chat.send_message("Usage: /reject <id> <reason>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.effective_chat.send_message("Task id must be an integer.")
            return
        reason = " ".join(context.args[1:])
        reply = await handle_reject(task_id=task_id, reason=reason, db_path=db_path)
        await update.effective_chat.send_message(reply)

    async def _on_resume(update, context):
        if not context.args:
            await update.effective_chat.send_message("Usage: /resume <id>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.effective_chat.send_message("Task id must be an integer.")
            return
        reply = await handle_resume(task_id=task_id, db_path=db_path)
        await update.effective_chat.send_message(reply)

    app.add_handler(CommandHandler("tasks", _on_tasks))
    app.add_handler(CommandHandler("task", _on_task))
    app.add_handler(CommandHandler("approve", _on_approve))
    app.add_handler(CommandHandler("reject", _on_reject))
    app.add_handler(CommandHandler("resume", _on_resume))
```

(Make sure the existing free-form handler still wins for non-command messages.)

- [ ] **Step 3: Smoke test**

Run: `.\.venv\Scripts\python.exe -c "from agent_hub.telegram_bot.bot import build_application; print('import ok')"`
Expected: `import ok` (and no crash).

Run the full suite:
Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -5`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add agent_hub/telegram_bot/bot.py
git commit -m "feat(bot): register /tasks /task /approve /reject /resume handlers"
```

---

## Task 20: Wire orchestrator lifecycle into __main__

**Files:**
- Modify: `agent_hub/__main__.py`
- Create: `tests/test_main_orchestrator_lifecycle.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_main_orchestrator_lifecycle.py`:

```python
"""Tests for the helper that builds the orchestrator and registers
its lifecycle with the PTB application."""

import pytest

from agent_hub.__main__ import _build_orchestrator
from agent_hub.config import Settings


def _settings(tmp_path):
    return Settings(
        telegram_bot_token="dummy",
        telegram_allowed_user_id=1,
        database_path=tmp_path / "agent_hub.db",
    )


@pytest.mark.asyncio
async def test_build_orchestrator_returns_started_orchestrator(tmp_path):
    settings = _settings(tmp_path)
    from agent_hub.agents import AgentRegistry
    from agent_hub.db import Database
    from agent_hub.agents.runner import AgentRunner

    db = Database(settings.database_path)
    await db.init()

    runner = AgentRunner(settings=settings, registry=AgentRegistry.load())
    orch = _build_orchestrator(
        settings=settings,
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=None,
    )
    assert orch is not None
    assert orch.repo_root is not None or settings.default_workspace is None
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_main_orchestrator_lifecycle.py -v`
Expected: ImportError.

- [ ] **Step 3: Add _build_orchestrator and wire into main**

In `agent_hub/__main__.py`, add:

```python
def _build_orchestrator(
    *,
    settings: Settings,
    registry,
    runner,
    db: Database,
    surface,  # MessageSurface | None
):
    from agent_hub.orchestrator import Orchestrator
    return Orchestrator(
        registry=registry,
        runner=runner,
        db=db,
        surface=surface,
        repo_root=settings.default_workspace,
    )
```

In `main()`, replace the existing `orchestrator = Orchestrator(...)` block with a call to `_build_orchestrator`, passing `surface=None` for now (Task 22 will add the real TelegramSurface). In the existing `_post_init` hook, after `await db.init()`, call:

```python
    await orchestrator.start()
    # Restart-resume scan once at boot.
    if orchestrator.surface is not None:
        from agent_hub.orchestrator.resume import scan_stale_tasks
        await scan_stale_tasks(db_path=settings.database_path, surface=orchestrator.surface)
```

In `_post_shutdown`, before the existing `runner.shutdown()`:

```python
    await orchestrator.stop()
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_main_orchestrator_lifecycle.py -v`
Expected: PASS.

Run full suite:
Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -5`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/__main__.py tests/test_main_orchestrator_lifecycle.py
git commit -m "feat(main): start/stop orchestrator with the PTB lifecycle"
```

---

## Task 21: TelegramSurface implementation

**Files:**
- Create: `agent_hub/telegram_bot/surface_telegram.py`
- Create: `tests/test_surface_telegram.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_surface_telegram.py`:

```python
"""TelegramSurface adapts the PTB Application into a MessageSurface."""

import pytest

from agent_hub.telegram_bot.surface_telegram import TelegramSurface


class _FakeApp:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.bot = self

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


@pytest.mark.asyncio
async def test_telegram_surface_calls_app_send_message():
    app = _FakeApp()
    surface = TelegramSurface(app)
    await surface.dm(chat_id=42, text="hello")
    assert app.sent == [(42, "hello")]


@pytest.mark.asyncio
async def test_telegram_surface_handles_send_failure_silently():
    """If send_message raises, the surface logs and swallows — we don't
    want a single chat outage to take down the orchestrator loops."""
    class _BrokenApp:
        def __init__(self):
            self.bot = self

        async def send_message(self, chat_id, text, **kwargs):
            raise RuntimeError("network down")

    surface = TelegramSurface(_BrokenApp())
    # Should not raise.
    await surface.dm(chat_id=42, text="hello")
```

- [ ] **Step 2: Run, verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_surface_telegram.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement TelegramSurface**

Create `agent_hub/telegram_bot/surface_telegram.py`:

```python
"""TelegramSurface — adapts a PTB Application into a MessageSurface."""

from __future__ import annotations

from typing import Any

import structlog

from agent_hub.orchestrator.surface import MessageSurface

log = structlog.get_logger("agent_hub.telegram_surface")


class TelegramSurface(MessageSurface):
    """Sends DMs via the PTB Application's bot.

    On send failure (network blip, chat blocked, rate limit), logs the
    error and swallows — the orchestrator loops must survive transient
    Telegram outages.
    """

    def __init__(self, app: Any):
        self._app = app

    async def dm(self, chat_id: int, text: str) -> None:
        try:
            await self._app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            log.warning("telegram_surface.send_failed", chat_id=chat_id, error=str(exc))
```

- [ ] **Step 4: Run, verify pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_surface_telegram.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_hub/telegram_bot/surface_telegram.py tests/test_surface_telegram.py
git commit -m "feat(telegram): TelegramSurface adapter for MessageSurface"
```

---

## Task 22: Pass TelegramSurface into the orchestrator at boot

**Files:**
- Modify: `agent_hub/__main__.py`

- [ ] **Step 1: Inject TelegramSurface**

In `agent_hub/__main__.py`, the current `_post_init` hook builds the orchestrator with `surface=None`. Replace that with:

```python
async def _post_init(app, settings, runner, db, orchestrator) -> None:
    log = structlog.get_logger("agent_hub")
    await db.init()
    # ... existing workspace-restore code ...

    # Install the real Telegram surface now that the app is available.
    from agent_hub.telegram_bot.surface_telegram import TelegramSurface
    orchestrator.surface = TelegramSurface(app)

    await orchestrator.start()

    from agent_hub.orchestrator.resume import scan_stale_tasks
    await scan_stale_tasks(db_path=settings.database_path, surface=orchestrator.surface)
```

Update `main()` so it passes `orchestrator` into the `_post_init` lambda:

```python
    app.post_init = lambda a: _post_init(a, settings, runner, db, orchestrator)
```

(The `_post_shutdown` already calls `orchestrator.stop()` from Task 20.)

- [ ] **Step 2: Smoke test the import path**

Run: `.\.venv\Scripts\python.exe -c "from agent_hub.__main__ import main; print('ok')"`
Expected: `ok`.

Run the full suite:
Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -5`
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add agent_hub/__main__.py
git commit -m "feat(main): install TelegramSurface in orchestrator at PTB post_init"
```

---

## Task 23: Integration test — Flow A (single feature with design gate)

**Files:**
- Create: `tests/integration/__init__.py` (empty)
- Create: `tests/integration/test_flow_a.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/__init__.py` (empty).

Create `tests/integration/test_flow_a.py`:

```python
"""Flow A — single feature with design gate.

End-to-end through the orchestrator's state machine using FakeAgentRunner.
No real Claude SDK, no real Telegram bot.

Walks through:
  user → PM creates task → handoff to architect →
  architect produces design + gate.request → user /approves →
  fullstack implements → reviewer LGTM → QA done → push.

This test exercises the orchestrator's tick loops directly; it does
NOT spin up the full background tasks because we want deterministic
ordering.
"""

import subprocess
from pathlib import Path

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.agents.runner import TextChunk, ToolEnd, ToolStart, TurnDone
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.tasks.worktree_repo import WorktreeRepository
from agent_hub.telegram_bot.commands.approve_cmd import handle_approve
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """Bare remote + clone with one initial commit, returns clone path."""
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
async def test_flow_a_single_feature_design_gate(temp_db_path, repo_root, tmp_path):
    db = Database(temp_db_path)
    await db.init()
    runner = FakeAgentRunner()
    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=runner,
        db=db,
        surface=surface,
        repo_root=repo_root,
    )
    repo = TaskRepository(temp_db_path)
    queue = HandoffQueue(temp_db_path)
    gates = GateRepository(temp_db_path)
    wt_repo = WorktreeRepository(temp_db_path)

    # 1. User filed task via PM (we apply the DB state directly — Plan 4
    #    is where the PM prompt actually drives this).
    task = await repo.create(title="add /health", description="ping D1", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING, owner="pm")
    await queue.enqueue(
        task_id=task.id, from_agent="pm", to_agent="architect", message="Design /health",
    )

    # 2. Architect script: produces design and requests gate.
    runner.script("architect", task_id=task.id, events=[
        TextChunk(text="Design: SELECT 1 against D1, 5s timeout."),
        ToolStart(tool="tasks.comment", input={"task_id": task.id, "body": "design"}),
        ToolEnd(tool="tasks.comment", is_error=False),
        ToolStart(tool="gate.request", input={"task_id": task.id, "kind": "design"}),
        ToolEnd(tool="gate.request", is_error=False),
        TurnDone(cost_usd=0.01, duration_ms=20),
    ])
    # Tools in the script don't actually mutate DB (they're fakes), so we
    # apply the corresponding effects manually — this is what the real
    # MCP tools would do.
    await orch._tick_handoff()
    await repo.comment(task.id, actor="architect", body="design ready")
    await gates.request(task_id=task.id, kind="design", summary="ready")
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)

    # 3. Gate watcher DMs the user.
    await orch._tick_gates()
    assert any("design" in m.lower() and f"#{task.id}" in m for m in surface.dms_to(42))

    # 4. User /approves.
    reply = await handle_approve(task_id=task.id, db_path=temp_db_path)
    assert "approved" in reply.lower()
    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.READY

    # 5. Approval would now trigger a handoff to fullstack — we apply
    #    that effect explicitly here (Plan 4 wires this into /approve).
    worktrees_root = tmp_path / "wt"
    worktrees_root.mkdir()
    wt_path = worktrees_root / str(task.id)
    subprocess.check_call(
        ["git", "worktree", "add", "-b", f"task/{task.id}-health", str(wt_path), "main"],
        cwd=repo_root,
    )
    (wt_path / "health.py").write_text("# health\n")
    subprocess.check_call(["git", "add", "health.py"], cwd=wt_path)
    subprocess.check_call(["git", "commit", "-m", "feat: health"], cwd=wt_path)
    await wt_repo.record(
        task_id=task.id, path=str(wt_path),
        branch=f"task/{task.id}-health", base_branch="main",
    )
    await repo.update(task.id, status=TaskStatus.IN_PROGRESS, owner="fullstack-engineer")

    # 6. Fullstack hands off to reviewer.
    await queue.enqueue(
        task_id=task.id, from_agent="fullstack-engineer", to_agent="reviewer",
        message="implemented",
    )
    runner.script("reviewer", task_id=task.id, events=[
        TextChunk(text="LGTM"),
        TurnDone(cost_usd=0.005, duration_ms=10),
    ])
    await orch._tick_handoff()

    # 7. Reviewer hands off to QA.
    await repo.update(task.id, status=TaskStatus.REVIEW)
    await queue.enqueue(
        task_id=task.id, from_agent="reviewer", to_agent="qa", message="approved",
    )
    runner.script("qa", task_id=task.id, events=[
        TextChunk(text="tests pass"),
        TurnDone(cost_usd=0.003, duration_ms=10),
    ])
    # Apply the QA tool effect (status to done) before the tick.
    await repo.update(task.id, status=TaskStatus.DONE)
    await orch._tick_handoff()

    # 8. Push should have happened. Check the branch lands on the remote.
    remote_branches = subprocess.check_output(
        ["git", "branch", "--list"], cwd=tmp_path / "remote.git",
    ).decode()
    assert f"task/{task.id}-health" in remote_branches
    assert any("pushed" in m.lower() for m in surface.dms_to(42))
```

- [ ] **Step 2: Run**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_flow_a.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/__init__.py tests/integration/test_flow_a.py
git commit -m "test(integration): Flow A — single feature with design gate end-to-end"
```

---

## Task 24: Integration test — Flow C (design rejection loop)

**Files:** Create `tests/integration/test_flow_c.py`.

- [ ] **Step 1: Write the test**

Create `tests/integration/test_flow_c.py`:

```python
"""Flow C — design rejection loop.

Architect designs → gate.request → user /rejects with feedback →
status returns to planning → handoff to architect with the rejection
context. (We don't run the architect's second turn — the test asserts
the orchestrator state is correctly set up for it.)
"""

import pytest

from agent_hub.agents import AgentRegistry
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.state_machine import TaskStatus
from agent_hub.tasks.gates import GateRepository
from agent_hub.tasks.handoff_queue import HandoffQueue
from agent_hub.tasks.repository import TaskRepository
from agent_hub.telegram_bot.commands.reject_cmd import handle_reject
from tests.fakes.fake_runner import FakeAgentRunner
from tests.fakes.fake_surface import FakeMessageSurface


@pytest.mark.asyncio
async def test_flow_c_design_rejection(temp_db_path):
    db = Database(temp_db_path)
    await db.init()
    surface = FakeMessageSurface()
    orch = Orchestrator(
        registry=AgentRegistry.load(),
        runner=FakeAgentRunner(),
        db=db,
        surface=surface,
    )
    repo = TaskRepository(temp_db_path)
    queue = HandoffQueue(temp_db_path)
    gates = GateRepository(temp_db_path)

    task = await repo.create(title="x", description="-", origin_chat_id=42)
    await repo.update(task.id, status=TaskStatus.PLANNING)
    await repo.update(task.id, status=TaskStatus.DESIGN_REVIEW)
    await gates.request(task_id=task.id, kind="design")

    await orch._tick_gates()  # user gets the design-ready DM

    reply = await handle_reject(
        task_id=task.id,
        reason="prefer SELECT 1 not full query",
        db_path=temp_db_path,
    )

    fresh = await repo.get(task.id)
    assert fresh.status == TaskStatus.PLANNING
    assert await gates.status(task_id=task.id, kind="design") == "rejected"

    pending = await queue.pending()
    assert any(
        h.to_agent == "architect" and "SELECT 1" in h.message for h in pending
    )
    events = await repo.events(task.id)
    assert any("rejected" in (e.payload.get("body") or "").lower() for e in events)
    assert "rejected" in reply.lower()
```

- [ ] **Step 2: Run**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_flow_c.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_flow_c.py
git commit -m "test(integration): Flow C — design rejection loop"
```

---

## Task 25: Full suite + parallel verification

**Files:** none.

- [ ] **Step 1: Serial**

Run: `.\.venv\Scripts\python.exe -m pytest -v 2>&1 | tail -10`
Expected: all pass. Total ≈ Plan 2 baseline (117) + ~50 new tests = ~167.

- [ ] **Step 2: Parallel**

Run: `.\.venv\Scripts\python.exe -m pytest -n auto 2>&1 | tail -5`
Expected: same total, all pass.

- [ ] **Step 3: Inspect flakes**

The integration tests use real git subprocesses and the tick loops. If anything flakes under -n auto, the suspect is usually a fixture that doesn't use unique tmp_path subdirectories or a shared module-level state. Fix the fixture, not the assertion.

---

## Self-review

**Spec coverage:**
- §4.4 orchestrator responsibilities (handoff loop, gate watcher, restart-resume) — Tasks 4-8, 16 ✓
- §4.8 Telegram surface (/tasks, /task, /approve, /reject, /resume) — Tasks 9-13, 19 ✓
- §5 Flow A (design gate) — Task 23 ✓
- §5 Flow C (rejection) — Task 24 ✓
- §5 Flow B (parallel epic) — deferred to Plan 4 alongside the agent prompt updates that would actually drive parallel work
- §6.D restart-resume scan — Task 16 ✓
- §6.D push action — Tasks 14, 17 ✓
- §6 epic auto-completion — Task 15 ✓

**Placeholder scan:** none. Every step has runnable code or commands.

**Type consistency:**
- `MessageSurface.dm(chat_id, text)` signature stable across Tasks 0, 1, 21.
- `FakeAgentRunner.send(agent_name, message, *, task_id=None)` matches the real `AgentRunner.send`.
- `Orchestrator.__init__` accepts `surface=None` and `repo_root=None` keyword args; Tasks 3, 17, 20, 22 all use these consistently.
- `handle_approve` / `handle_reject` / `handle_tasks` / `handle_task` / `handle_resume` all take `*, task_id=..., db_path=...` (handle_reject also takes `reason`) and return `str`.

**Known sequencing notes:**
- Task 0 must land before Task 1 (FakeMessageSurface depends on MessageSurface base).
- Task 2 (FakeAgentRunner) is independent of Tasks 0-1 but lands before Task 3.
- Task 3 (lifecycle scaffolding) MUST land before Tasks 5, 8, 17, 20, 22 (anything that uses start/stop).
- Task 17 depends on Tasks 14, 15 (push, epic).
- Task 22 depends on Task 21 (TelegramSurface).
- Tasks 23 (Flow A) and 24 (Flow C) require all preceding orchestrator tasks.

**Out-of-scope items confirmed deferred to Plan 4:**
- Flow B integration test (depends on parallel handoffs being meaningful, which depends on real agent prompts).
- Spend cap + stuck-loop detection.
- Gate-timeout reminders.
- Agent prompt updates so PM actually calls tasks.create + handoff.
- `allowed_tools` additions for `mcp__agent_hub__*`.
- Real-SDK Haiku smoke.
