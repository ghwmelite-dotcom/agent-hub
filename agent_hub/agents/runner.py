"""Wraps the Claude Agent SDK so each role is a persistent, addressable agent.

Each agent gets its own ClaudeSDKClient keyed on (agent_name, task_id).
Conversations persist across turns within a process; the client is created
lazily on first use and reused for every subsequent message to that role +
task pair.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from agent_hub.agents.registry import AgentRegistry, AgentRole
from agent_hub.config import Settings

log = structlog.get_logger(__name__)


def _client_factory(options: Any) -> Any:
    """Default factory — constructs the real ClaudeSDKClient.

    Tests monkey-patch this symbol to inject a fake client.
    """
    import claude_agent_sdk as sdk

    return sdk.ClaudeSDKClient(options=options)


@dataclass
class TextChunk:
    """A piece of streamed text from the agent."""

    text: str


@dataclass
class ToolStart:
    """The agent invoked a tool."""

    tool: str
    input: dict[str, Any]


@dataclass
class ToolEnd:
    """A tool call finished."""

    tool: str
    is_error: bool


@dataclass
class TurnDone:
    """The agent's turn has ended."""

    cost_usd: float | None = None
    duration_ms: int | None = None


@dataclass
class AgentError:
    """Something went wrong serving this turn."""

    message: str


AgentEvent = TextChunk | ToolStart | ToolEnd | TurnDone | AgentError


class AgentRunner:
    """One persistent Claude session per (agent role, task_id) pair."""

    def __init__(self, settings: Settings, registry: AgentRegistry):
        self.settings = settings
        self.registry = registry
        self._clients: dict[tuple[str, int | None], Any] = {}
        self._lock = asyncio.Lock()
        self._cwd: Path | None = settings.default_workspace

    # ------------------------------------------------------------------
    # Workspace
    # ------------------------------------------------------------------

    def set_workspace(self, path: Path) -> None:
        """Change which directory new agents will use as their working dir.

        Existing agents keep the cwd they were started with — call `reset`
        to relaunch them in the new workspace.
        """
        self._cwd = path

    @property
    def workspace(self) -> Path | None:
        return self._cwd

    @property
    def active_session_count(self) -> int:
        """Number of live (agent, task_id) SDK sessions — for /status."""
        return len(self._clients)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        async with self._lock:
            for key, client in list(self._clients.items()):
                try:
                    await client.disconnect()
                except Exception as exc:  # noqa: BLE001
                    log.warning("agent.shutdown_failed", key=key, error=str(exc))
            self._clients.clear()

    async def reset(self, agent_name: str, *, task_id: int | None = None) -> None:
        """Drop an agent's session — next message starts a fresh context.

        Disconnects the live SDK client AND forgets the persisted session
        UUID, so the next reconnect generates a new conversation rather
        than resuming the (presumably broken) one we just abandoned.
        """
        from agent_hub.agents.session_store import AgentSessionStore

        canonical = self.registry.resolve(agent_name)
        if canonical is None:
            raise KeyError(agent_name)
        async with self._lock:
            client = self._clients.pop((canonical, task_id), None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "agent.reset_failed",
                    agent=canonical,
                    task_id=task_id,
                    error=str(exc),
                )
        await AgentSessionStore(self.settings.database_path).forget(
            agent_name=canonical, task_id=task_id,
        )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send(
        self,
        agent_name: str,
        message: str,
        *,
        task_id: int | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Send a message to an agent and stream events back.

        Yields TextChunk / ToolStart / ToolEnd events as the agent works, and
        finally a TurnDone (or AgentError) event when the turn ends.
        """
        role = self.registry.get(agent_name)
        client = await self._get_or_create_client(
            role.name, task_id=task_id, cwd=self._cwd
        )

        try:
            await client.query(message)
        except Exception as exc:  # noqa: BLE001
            log.exception("agent.query_failed", agent=role.name)
            yield AgentError(message=f"Failed to send message: {exc}")
            return

        try:
            async for msg in client.receive_response():
                for event in _events_from_message(msg):
                    yield event
        except Exception as exc:  # noqa: BLE001
            log.exception("agent.stream_failed", agent=role.name)
            yield AgentError(message=f"Stream error: {exc}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _get_or_create_client(
        self,
        agent_name: str,
        *,
        task_id: int | None,
        cwd: Path | None,
    ) -> Any:
        from agent_hub.agents.runner_options import build_sdk_options
        from agent_hub.agents.session_store import AgentSessionStore
        from agent_hub.tasks.worktree_repo import WorktreeRepository

        role = self.registry.get(agent_name)
        key = (role.name, task_id)
        async with self._lock:
            if key in self._clients:
                return self._clients[key]

            # Resolve effective cwd:
            # 1. If task_id has a recorded (not-cleaned) worktree → use it.
            # 2. Else if caller passed an explicit cwd → use it.
            # 3. Else fall back to global workspace (self._cwd).
            effective_cwd: Path | None = None
            if task_id is not None:
                wt_repo = WorktreeRepository(self.settings.database_path)
                row = await wt_repo.get_by_task(task_id)
                if row is not None and row.cleaned_at is None:
                    effective_cwd = Path(row.path)
            if effective_cwd is None and cwd is not None:
                effective_cwd = cwd
            if effective_cwd is None:
                effective_cwd = self._cwd

            # Fingerprint-based session refresh: if memory has changed
            # since this session was last attached, drop the session UUID
            # so the SDK rebuilds the system prompt with current memory.
            from agent_hub.memory.store import MemoryStore
            session_store = AgentSessionStore(self.settings.database_path)
            if effective_cwd is not None:
                current_fp = await MemoryStore(self.settings.database_path).fingerprint(
                    workspace=str(effective_cwd), agent_name=role.name,
                )
                stored_fp = await session_store.get_fingerprint(
                    agent_name=role.name, task_id=task_id,
                )
                if stored_fp is not None and stored_fp != current_fp:
                    await session_store.forget(
                        agent_name=role.name, task_id=task_id,
                    )
                await session_store.set_fingerprint(
                    agent_name=role.name, task_id=task_id,
                    fingerprint=current_fp,
                )

            session_id = await session_store.get_or_create(
                agent_name=role.name, task_id=task_id,
            )

            options = await build_sdk_options(
                role,
                cwd=effective_cwd,
                db_path=self.settings.database_path,
                session_id=session_id,
            )
            client = _client_factory(options)
            await client.connect()
            self._clients[key] = client
            log.info(
                "agent.started",
                agent=role.name,
                task_id=task_id,
                model=role.model,
                tools=role.allowed_tools,
                cwd=str(effective_cwd) if effective_cwd else None,
            )
            return client


# ----------------------------------------------------------------------
# Message → event translation
# ----------------------------------------------------------------------


def _events_from_message(msg: Any) -> list[AgentEvent]:
    """Translate an SDK message into AgentEvent(s).

    The SDK exposes several message classes (AssistantMessage, UserMessage,
    SystemMessage, ResultMessage). We do attribute-duck-typing instead of
    isinstance checks so this keeps working across minor SDK revisions.
    """
    events: list[AgentEvent] = []

    # Assistant messages carry content blocks: TextBlock, ToolUseBlock,
    # ToolResultBlock. We surface text + tool starts.
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                events.append(TextChunk(text=text))
                continue
            tool_name = getattr(block, "name", None)
            tool_input = getattr(block, "input", None)
            tool_use_id = getattr(block, "id", None)
            if tool_name and tool_use_id and tool_input is not None:
                events.append(
                    ToolStart(tool=str(tool_name), input=dict(tool_input))
                )
                continue
            # ToolResultBlock
            if hasattr(block, "tool_use_id") and getattr(block, "content", None) is not None:
                events.append(
                    ToolEnd(
                        tool="",  # name not on result block; bot infers from order
                        is_error=bool(getattr(block, "is_error", False)),
                    )
                )

    # ResultMessage — turn complete, has cost + duration.
    if (
        getattr(msg, "total_cost_usd", None) is not None
        or getattr(msg, "duration_ms", None) is not None
    ):
        events.append(
            TurnDone(
                cost_usd=getattr(msg, "total_cost_usd", None),
                duration_ms=getattr(msg, "duration_ms", None),
            )
        )

    return events
