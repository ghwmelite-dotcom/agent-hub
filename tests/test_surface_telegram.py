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


@pytest.mark.asyncio
async def test_render_event_produces_editorial_markdownv2():
    """End-to-end: simulate an agent's event sequence and verify the
    accumulated text on the streamed message matches the expected
    MarkdownV2 layout (header, prose, tool line, more prose)."""
    from agent_hub.agents.runner import TextChunk, ToolStart, ToolEnd
    from agent_hub.telegram_bot.bot import _render_event
    from agent_hub.telegram_bot.formatting import role_header
    from agent_hub.telegram_bot.streamer import StreamingMessage

    class _FakeMessage:
        def __init__(self):
            self.text = ""
            self.parse_mode = None

        async def edit_text(self, text: str, **kwargs):
            self.text = text
            self.parse_mode = kwargs.get("parse_mode")

    class _FakeBot:
        def __init__(self):
            self.last_message = None
            self.last_parse_mode = None

        async def send_message(self, chat_id, text, **kwargs):
            msg = _FakeMessage()
            msg.text = text
            msg.parse_mode = kwargs.get("parse_mode")
            self.last_message = msg
            self.last_parse_mode = kwargs.get("parse_mode")
            return msg

    bot = _FakeBot()
    stream = StreamingMessage(
        chat_id=42, bot=bot, prefix=role_header("Reviewer"),
    )

    # Disable throttling so each event flushes immediately.
    async def _flush_now(stream, event):
        stream.last_edit_at = 0
        await _render_event(stream, event)

    await _flush_now(stream, TextChunk(text="All three fixes verified. "))
    await _flush_now(stream, ToolStart(
        tool="Read", input={"file_path": "DigestFeatured.tsx"},
    ))
    await _flush_now(stream, ToolEnd(tool="Read", is_error=False))
    await _flush_now(stream, TextChunk(text="**Blocker 1**: `s.isSlow` is replaced."))
    await stream.finalize()

    # The bubble's text should contain:
    # - Role header (escaped, MarkdownV2)
    # - Escaped prose
    # - Italic tool line with humanized name
    # - GitHub-style **bold** translated to *bold*
    final = stream.current_message.text
    assert "▍ *Reviewer*" in final
    assert "All three fixes verified\\." in final
    assert "_› Read DigestFeatured\\.tsx_" in final
    assert "*Blocker 1*" in final
    assert "`s.isSlow`" in final
    # parse_mode must have been MarkdownV2 on the send_message call.
    assert bot.last_parse_mode == "MarkdownV2"
