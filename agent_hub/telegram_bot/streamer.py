"""Telegram message streaming with throttled edits.

Telegram's Bot API rate-limits message edits to roughly one per second per
chat. We accumulate text from the agent and edit the message at most every
1.5 seconds. When the accumulated text exceeds the per-message character
limit (4096), we send a new message and continue there.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from telegram import Message
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError

TELEGRAM_MAX_CHARS = 4000  # Slightly under the 4096 hard limit for safety.
EDIT_INTERVAL_SECONDS = 1.5


@dataclass
class StreamingMessage:
    """One streamed reply from an agent, possibly spanning several Telegram
    messages once the text crosses the per-message length cap."""

    chat_id: int
    bot: object  # telegram.Bot, kept loose to avoid heavy type imports here
    prefix: str = ""
    current_message: Message | None = None
    current_text: str = ""
    last_edit_at: float = 0.0
    pending_text: str = ""
    sent_messages: list[Message] = field(default_factory=list)

    async def append(self, chunk: str) -> None:
        """Add a chunk of text and edit/post as needed."""
        if not chunk:
            return

        self.pending_text += chunk
        now = time.monotonic()

        # Send the first message immediately so the user sees activity.
        if self.current_message is None:
            await self._send_initial()
            return

        if now - self.last_edit_at < EDIT_INTERVAL_SECONDS:
            return

        await self._flush()

    async def finalize(self) -> None:
        """Force-flush any pending text. Call when the agent's turn ends."""
        if self.pending_text:
            await self._flush(force=True)

    # ------------------------------------------------------------------

    async def _send_initial(self) -> None:
        text = (self.prefix + self.pending_text)[:TELEGRAM_MAX_CHARS]
        self.current_message = await self.bot.send_message(  # type: ignore[attr-defined]
            chat_id=self.chat_id,
            text=text or self.prefix or "…",
        )
        self.sent_messages.append(self.current_message)
        self.current_text = text
        self.pending_text = self.pending_text[len(text) - len(self.prefix):]
        self.last_edit_at = time.monotonic()

    async def _flush(self, force: bool = False) -> None:
        """Push pending_text into current_message (or roll to a new one)."""
        assert self.current_message is not None

        new_text = self.current_text + self.pending_text
        # If it overflows, split: edit current to the cutoff, send rest as new.
        if len(new_text) > TELEGRAM_MAX_CHARS:
            cutoff = _natural_split(new_text, TELEGRAM_MAX_CHARS)
            keep = new_text[:cutoff]
            overflow = new_text[cutoff:]
            await self._safe_edit(keep)
            self.current_text = keep
            self.pending_text = ""

            # Start a new message for the overflow. Continuation marker
            # keeps it readable on mobile.
            self.current_message = await self.bot.send_message(  # type: ignore[attr-defined]
                chat_id=self.chat_id,
                text=overflow[:TELEGRAM_MAX_CHARS] or "…",
            )
            self.sent_messages.append(self.current_message)
            self.current_text = overflow[:TELEGRAM_MAX_CHARS]
            self.pending_text = overflow[TELEGRAM_MAX_CHARS:]
            self.last_edit_at = time.monotonic()
            return

        await self._safe_edit(new_text)
        self.current_text = new_text
        self.pending_text = ""
        self.last_edit_at = time.monotonic()

    async def _safe_edit(self, text: str) -> None:
        assert self.current_message is not None
        try:
            await self.current_message.edit_text(text=text)
        except RetryAfter as exc:
            # Hit Telegram's rate limit — wait and retry once.
            await asyncio.sleep(exc.retry_after + 0.1)
            try:
                await self.current_message.edit_text(text=text)
            except TelegramError:
                pass
        except BadRequest:
            # "Message is not modified" or similar — ignore.
            pass
        except TelegramError:
            pass


def _natural_split(text: str, max_len: int) -> int:
    """Find a nice place to break text near max_len — paragraph > sentence > word."""
    window = text[: max_len + 1]
    for sep in ("\n\n", "\n", ". ", " "):
        idx = window.rfind(sep)
        if idx > max_len // 2:  # don't split absurdly early
            return idx + len(sep)
    return max_len
