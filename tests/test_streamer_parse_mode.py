"""Tests for StreamingMessage's parse_mode handling and fallback."""

from __future__ import annotations

import asyncio

import pytest
from telegram.error import BadRequest

from agent_hub.telegram_bot.streamer import StreamingMessage


class _FakeMessage:
    """Stand-in for telegram.Message that records edit calls."""

    def __init__(self, message_id: int, bot: "_FakeBot"):
        self.message_id = message_id
        self.bot = bot
        self.edits: list[dict] = []
        self._fail_next_parse: bool = False

    async def edit_text(self, text: str, **kwargs):
        if self._fail_next_parse and kwargs.get("parse_mode") == "MarkdownV2":
            self._fail_next_parse = False
            raise BadRequest("can't parse entities: bad escape")
        self.edits.append({"text": text, **kwargs})


class _FakeBot:
    def __init__(self):
        self.sent: list[dict] = []
        self._next_id = 1
        self.next_message_fails_parse: bool = False

    async def send_message(self, chat_id: int, text: str, **kwargs):
        msg = _FakeMessage(self._next_id, self)
        self._next_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        if self.next_message_fails_parse:
            msg._fail_next_parse = True
            self.next_message_fails_parse = False
        return msg


@pytest.mark.asyncio
async def test_send_initial_uses_markdownv2():
    bot = _FakeBot()
    stream = StreamingMessage(chat_id=42, bot=bot, prefix="▍ *Hi*\n\n")
    await stream.append("hello world")
    # The first send_message call must have parse_mode set.
    assert bot.sent[0]["parse_mode"] == "MarkdownV2"


@pytest.mark.asyncio
async def test_safe_edit_falls_back_to_plain_on_parse_error(monkeypatch):
    """When MarkdownV2 parsing fails on an edit, retry once without parse_mode."""
    bot = _FakeBot()
    stream = StreamingMessage(chat_id=42, bot=bot, prefix="hdr ")
    await stream.append("first")           # Triggers send_initial
    msg = stream.current_message
    msg._fail_next_parse = True            # Next edit will raise BadRequest

    # Force the throttle window to expire so _flush actually edits.
    stream.last_edit_at = 0
    await stream.append(" second")

    # The retry should have landed: there must be at least one edit
    # recorded, and the latest one must NOT have parse_mode=MarkdownV2.
    assert msg.edits, "expected a retry edit after the parse failure"
    assert msg.edits[-1].get("parse_mode") is None


@pytest.mark.asyncio
async def test_continuation_bubble_omits_role_header():
    """When text overflows 4096 chars, the new bubble's send_message
    must not include the prefix (role header) — only the overflow text."""
    bot = _FakeBot()
    prefix = "▍ *Pm*\n\n"
    stream = StreamingMessage(chat_id=42, bot=bot, prefix=prefix)
    big = "x" * 5000          # > TELEGRAM_MAX_CHARS (4000)
    await stream.append(big)
    stream.last_edit_at = 0   # allow flush
    await stream.append("y")  # trigger flush + overflow

    # First send_message carries the prefix; second one does NOT.
    assert bot.sent[0]["text"].startswith(prefix)
    assert len(bot.sent) >= 2
    assert not bot.sent[1]["text"].startswith(prefix)
