"""Formatting helpers for Telegram agent output.

Pure functions only — no I/O, no global state, no Telegram client.
The streamer and bot.py call these to convert agent events into
MarkdownV2-safe strings.

Style: "editorial" — bold role header on the first bubble, compact
italic tool-call lines, prose treated as MarkdownV2 source.
"""

from __future__ import annotations

from telegram.helpers import escape_markdown


def escape(text: str) -> str:
    """Backslash-escape every MarkdownV2 reserved character."""
    return escape_markdown(text, version=2)


def role_header(display_name: str) -> str:
    """First line + blank line that opens an agent's turn.

    Example: role_header("Reviewer") -> "▍ *Reviewer*\\n\\n"
    """
    return f"▍ *{escape(display_name)}*\n\n"
