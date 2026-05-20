"""Tests for the Telegram formatting helpers."""

from __future__ import annotations

from agent_hub.telegram_bot.formatting import escape, role_header


def test_escape_dot():
    assert escape("hello.world") == "hello\\.world"


def test_escape_all_reserved_chars():
    text = "_*[]()~`>#+-=|{}.!"
    out = escape(text)
    # Every reserved char must be backslash-prefixed.
    for ch in text:
        assert f"\\{ch}" in out


def test_escape_passes_safe_chars():
    assert escape("abc123 XYZ") == "abc123 XYZ"


def test_role_header_renders():
    assert role_header("Reviewer") == "▍ *Reviewer*\n\n"


def test_role_header_escapes_dots_in_name():
    assert role_header("QA.Bot") == "▍ *QA\\.Bot*\n\n"
