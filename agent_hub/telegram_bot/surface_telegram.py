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
