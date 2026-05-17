"""Routes incoming user messages to the right agent.

Routing rules (in order):
  1. Explicit @mention at the start of the message  → that agent.
     e.g. `@architect what about auth?` → architect
  2. Sticky thread — if the user is mid-conversation with an agent
     (tracked per Telegram chat), keep talking to them.
  3. Default → PM.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import structlog

from agent_hub.agents import AgentRegistry, AgentRunner
from agent_hub.agents.runner import AgentEvent
from agent_hub.db import Database

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
        default_agent: str = "pm",
    ):
        self.registry = registry
        self.runner = runner
        self.db = db
        self.default_agent = default_agent
        # Per-chat sticky agent — last agent the user was talking to.
        self._sticky: dict[int, str] = {}

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
