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
