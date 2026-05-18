"""Routes incoming user messages to the right agent.

Routing rules (in order):
  1. Explicit @mention at the start of the message  → that agent.
     e.g. `@architect what about auth?` → architect
  2. Sticky thread — if the user is mid-conversation with an agent
     (tracked per Telegram chat), keep talking to them.
  3. Default → PM.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog

from agent_hub.agents import AgentRegistry, AgentRunner
from agent_hub.agents.runner import AgentEvent
from agent_hub.db import Database
from agent_hub.orchestrator.surface import MessageSurface
from agent_hub.state_machine import TaskStatus

log = structlog.get_logger(__name__)

_MENTION_RE = re.compile(r"^@(\w+)\b\s*", re.UNICODE)


@dataclass
class RoutedMessage:
    agent: str
    display_name: str
    text: str


def parse_addressee(
    message: str,
    registry: AgentRegistry,
    default: str = "pm",
    sticky: str | None = None,
) -> RoutedMessage:
    """Decide which agent should receive this message.

    Returns the canonical agent name and the stripped message text.
    """
    match = _MENTION_RE.match(message)
    if match:
        candidate = match.group(1)
        canonical = registry.resolve(candidate)
        if canonical:
            stripped = message[match.end():].strip()
            return RoutedMessage(
                agent=canonical,
                display_name=registry.get(canonical).display_name,
                text=stripped or "(no message)",
            )
        # @something that isn't an agent — fall through to default.
        log.info("router.unknown_mention", mention=candidate)

    target = sticky or default
    canonical = registry.resolve(target) or registry.resolve(default)
    if canonical is None:
        raise RuntimeError(f"Default agent {default!r} not found in registry")
    return RoutedMessage(
        agent=canonical,
        display_name=registry.get(canonical).display_name,
        text=message.strip(),
    )


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


class Orchestrator:
    """Glues the bot to the agent runner and persists conversations."""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        runner: AgentRunner,
        db: Database,
        surface: MessageSurface | None = None,
        repo_root: Path | None = None,
        default_agent: str = "pm",
        handoff_worker_count: int = 1,
        gate_reminder_hours: float = 24.0,
        stuck_turn_threshold: int = 12,
    ):
        self.registry = registry
        self.runner = runner
        self.db = db
        self.surface = surface
        self.repo_root = repo_root
        self.default_agent = default_agent
        self.handoff_worker_count = max(1, handoff_worker_count)
        self.gate_reminder_hours = gate_reminder_hours
        self.stuck_turn_threshold = stuck_turn_threshold
        # Per-chat sticky agent — last agent the user was talking to.
        self._sticky: dict[int, str] = {}
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._started = False
        # `notified_at` is persisted on gates so restart doesn't re-DM.
        # Set by start(); read by scan_stale_tasks to surface in the boot DM.
        self.released_stale_claims: int = 0
        # Budget cap: DM the user once when dispatch pauses, then go quiet
        # until spend drops back below the cap (or the cap is raised).
        self._cap_dm_sent: bool = False

    def sticky_for(self, chat_id: int) -> str | None:
        return self._sticky.get(chat_id)

    def set_sticky(self, chat_id: int, agent: str) -> None:
        self._sticky[chat_id] = agent

    def clear_sticky(self, chat_id: int) -> None:
        self._sticky.pop(chat_id, None)

    async def handle(
        self,
        *,
        chat_id: int,
        message: str,
    ) -> AsyncIterator[tuple[str, AgentEvent]]:
        """Route a user message, stream agent events.

        Yields (agent_display_name, event) tuples. The first yield will be a
        synthetic TextChunk with empty text — bots can use it to set up an
        initial placeholder message.
        """
        routed = parse_addressee(
            message,
            self.registry,
            default=self.default_agent,
            sticky=self._sticky.get(chat_id),
        )

        await self.db.log_message(
            agent=routed.agent,
            direction="in",
            content=routed.text,
            metadata={"chat_id": chat_id},
        )

        self._sticky[chat_id] = routed.agent

        log.info(
            "router.dispatch",
            agent=routed.agent,
            chat_id=chat_id,
            chars=len(routed.text),
        )

        # PM needs the chat_id to set `origin_chat_id` when it calls
        # `tasks_create` for fresh user messages. The handoff path
        # already prefixes with `[task #N, ...]` (which carries the
        # chat_id transitively via the task row), so this only matters
        # for direct user→@pm messages from `handle()`.
        if routed.agent == "pm":
            dispatch_text = f"[chat_id={chat_id}] {routed.text}"
        else:
            dispatch_text = routed.text

        accumulated: list[str] = []
        async for event in self.runner.send(routed.agent, dispatch_text):
            yield routed.display_name, event
            # Accumulate text for persistence.
            text = getattr(event, "text", None)
            if isinstance(text, str):
                accumulated.append(text)

        if accumulated:
            await self.db.log_message(
                agent=routed.agent,
                direction="out",
                content="".join(accumulated),
                metadata={"chat_id": chat_id},
            )

    async def start(self) -> None:
        """Start the background loops. Raises if called twice.

        Releases any handoff rows claimed by a previous (now dead) process
        before spinning up the tick loops — otherwise stuck tasks would
        sit forever waiting for an orchestrator that no longer exists.
        """
        if self._started:
            raise RuntimeError("Orchestrator.start() called twice")
        self._started = True
        self._stop_event.clear()

        from agent_hub.tasks.handoff_queue import HandoffQueue
        released = await HandoffQueue(self.db.path).release_stale_claims()
        if released:
            log.info("orchestrator.released_stale_claims", count=released)
        self.released_stale_claims = released

        for i in range(self.handoff_worker_count):
            self._tasks.append(
                asyncio.create_task(self._run_handoff_loop(), name=f"handoff-worker-{i}")
            )
        self._tasks.append(asyncio.create_task(self._run_gate_watcher()))

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

    async def _check_budget_cap(self) -> bool:
        """Return True if dispatch is allowed (cap unset or not exceeded).

        On the first tick where the cap is exceeded, DM the user once.
        When spend drops back below the cap (e.g. user raised it), reset
        the DM flag so future excursions get fresh notifications.
        """
        from agent_hub.telegram_bot.commands.budget_cmd import get_budget_cap
        from agent_hub.tasks.repository import TaskRepository

        cap = await get_budget_cap(self.db.path)
        if cap is None:
            return True

        repo = TaskRepository(self.db.path)
        spent = await repo.total_cost_usd()
        if spent < cap:
            self._cap_dm_sent = False
            return True

        if not self._cap_dm_sent and self.surface is not None:
            chat_id = await self._first_active_chat_id()
            if chat_id is not None:
                await self.surface.dm(
                    chat_id,
                    (
                        f"💰 Budget cap of ${cap:.2f} reached "
                        f"(spent ${spent:.4f}). Dispatch paused.\n"
                        f"`/budget <amount>` to raise, `/budget off` to remove."
                    ),
                )
            self._cap_dm_sent = True
        return False

    async def _first_active_chat_id(self) -> int | None:
        """Find one chat_id that has an in-flight (non-terminal) task.

        Used to address the cap-reached DM somewhere visible. Returns
        None when no task is active — no DM is needed in that case.
        """
        import aiosqlite
        async with aiosqlite.connect(self.db.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT origin_chat_id FROM tasks "
                "WHERE status NOT IN ('done', 'blocked') "
                "ORDER BY updated_at DESC LIMIT 1"
            )
            row = await cur.fetchone()
        return int(row["origin_chat_id"]) if row else None

    async def _tick_handoff(self) -> None:
        """Claim at most one handoff queue row and dispatch it."""
        from agent_hub.agents.runner import TextChunk, TurnDone
        from agent_hub.tasks.handoff_queue import HandoffQueue
        from agent_hub.tasks.repository import TaskRepository

        if not await self._check_budget_cap():
            return

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
            elif isinstance(event, TurnDone) and event.cost_usd:
                await repo.add_cost(row.task_id, event.cost_usd)

        if self.surface is not None and chat_id is not None and accumulated:
            body = "".join(accumulated).strip()
            if body:
                await self.surface.dm(chat_id, f"@{row.to_agent}: {body}")

        # Post-turn: did the task land in done?
        post_turn_task = await repo.get(row.task_id)
        if post_turn_task is not None and post_turn_task.status == TaskStatus.DONE:
            await self._on_task_done(post_turn_task)

    async def _on_task_done(self, task) -> None:
        """Called when an agent's turn left a task in done status.

        Pushes the branch, runs epic auto-completion, and DMs the user.
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

    async def _tick_gates(self) -> None:
        """Detect pending design gates and DM the user. Idempotent —
        each gate is announced at most once *ever* (state persisted via
        `gates.notified_at`, so restart doesn't re-DM)."""
        if self.surface is None:
            return
        from agent_hub.tasks.gates import GateRepository
        from agent_hub.tasks.repository import TaskRepository

        repo = TaskRepository(self.db.path)
        gates = GateRepository(self.db.path)

        unnotified = await gates.unresolved_unnotified()
        for gate in unnotified:
            task = await repo.get(gate.task_id)
            if task is None:
                continue
            summary = gate.summary or ""
            msg = (
                f"🛂 Task #{task.id} design ready"
                + (f": {summary}" if summary else "")
                + f"\nReply /approve {task.id} or /reject {task.id} <reason>."
            )
            await self.surface.dm(task.origin_chat_id, msg)
            await gates.mark_notified(gate.id)

        # Second pass: gates that have been pending too long get a nudge.
        # Threshold defaults to 24h via GateRepository.needing_reminder.
        reminders = await gates.needing_reminder(
            timeout_hours=self.gate_reminder_hours,
        )
        for gate in reminders:
            task = await repo.get(gate.task_id)
            if task is None:
                continue
            requested_at = gate.requested_at
            age_hours = (
                datetime.now(timezone.utc) - requested_at
            ).total_seconds() / 3600
            summary = gate.summary or ""
            msg = (
                f"⏰ Reminder: task #{task.id} has been awaiting your "
                f"design approval for ~{age_hours:.0f}h"
                + (f" — {summary}" if summary else "")
                + f"\nReply /approve {task.id} or /reject {task.id} <reason>."
            )
            await self.surface.dm(task.origin_chat_id, msg)
            await gates.mark_reminder_sent(gate.id)

    async def _tick_stuck_tasks(self) -> None:
        """DM the user when a non-terminal task has hit the turn ceiling
        without a status change.

        Idempotent: a `stuck_alert` event is appended after the DM and
        the next tick won't re-fire until the task's status changes again.
        """
        if self.surface is None:
            return
        from agent_hub.state_machine import TaskStatus
        from agent_hub.tasks.repository import TaskRepository

        repo = TaskRepository(self.db.path)
        active = await repo.list()
        for task in active:
            if task.status in (TaskStatus.DONE, TaskStatus.BLOCKED):
                continue
            turns = await repo.turns_since_status_change(task.id)
            if turns < self.stuck_turn_threshold:
                continue
            if await repo.stuck_alert_pending(task.id):
                continue
            msg = (
                f"🚨 Task #{task.id} ({task.title!r}) looks stuck — "
                f"{turns} agent turns since the last status change "
                f"(currently `{task.status.value}`).\n"
                f"`/task {task.id}` for details, `/cancel {task.id}` to "
                f"abort, or let it continue."
            )
            await self.surface.dm(task.origin_chat_id, msg)
            await repo.record_stuck_alert(task.id, turn_count=turns)

    async def _run_handoff_loop(self) -> None:
        loop_log = structlog.get_logger("agent_hub.handoff_loop")
        while not self._stop_event.is_set():
            try:
                await self._tick_handoff()
            except Exception as exc:
                loop_log.exception("handoff_loop.tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass

    async def _run_gate_watcher(self) -> None:
        log = structlog.get_logger("agent_hub.gate_watcher")
        while not self._stop_event.is_set():
            try:
                await self._tick_gates()
                await self._tick_stuck_tasks()
            except Exception as exc:
                log.exception("gate_watcher.tick_failed", error=str(exc))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
