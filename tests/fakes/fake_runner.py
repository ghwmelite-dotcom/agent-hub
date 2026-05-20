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

    # Matches the real AgentRunner.workspace property; None means "no workspace
    # configured", which makes on_handoff_kickback a no-op in the dispatch loop.
    workspace = None

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
