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
    await surface.dm(chat_id=42, text="hello")  # should not raise
