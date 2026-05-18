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

import structlog

from agent_hub.agents import AgentRegistry, AgentRunner
from agent_hub.agents.runner import AgentEvent
from agent_hub.db import Database
from agent_hub.orchestrator.surface import MessageSurface

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


class Orchestrator:
    """Glues the bot to the agent runner and persists conversations."""

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
        # Per-chat sticky agent — last agent the user was talking to.
        self._sticky: dict[int, str] = {}
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._started = False
        self._notified_gates: set[int] = set()

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

        accumulated: list[str] = []
        async for event in self.runner.send(routed.agent, routed.text):
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
        """Start the background loops. Raises if called twice."""
        if self._started:
            raise RuntimeError("Orchestrator.start() called twice")
        self._started = True
        self._stop_event.clear()
        self._tasks.append(asyncio.create_task(self._run_handoff_loop()))

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

    async def _tick_handoff(self) -> None:
        """Claim at most one handoff queue row and dispatch it."""
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

    async def _tick_gates(self) -> None:
        """Detect pending design gates and DM the user. Idempotent —
        each gate is announced at most once per orchestrator lifetime."""
        if self.surface is None:
            return
        import aiosqlite
        from agent_hub.tasks.repository import TaskRepository

        repo = TaskRepository(self.db.path)

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
