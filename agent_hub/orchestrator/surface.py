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
