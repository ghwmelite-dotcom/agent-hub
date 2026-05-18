"""Tests for the auth-mode resolver in __main__._apply_auth_mode.

Exercises the env-scrubbing behavior that forces subscription auth even
when an `ANTHROPIC_API_KEY` is present in the environment.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from agent_hub.__main__ import _apply_auth_mode
from agent_hub.config import Settings


def _settings(mode: str) -> Settings:
    return Settings(
        telegram_bot_token="dummy",
        telegram_allowed_user_id=1,
        anthropic_auth_mode=mode,  # type: ignore[arg-type]
    )


@pytest.fixture
def quiet_log():
    return MagicMock()


@pytest.fixture
def isolated_env(monkeypatch):
    """Each test gets a fresh ANTHROPIC_API_KEY-free env."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield


def test_subscription_mode_scrubs_existing_api_key(monkeypatch, quiet_log):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    _apply_auth_mode(_settings("subscription"), quiet_log)
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_subscription_mode_logs_scrub_event(monkeypatch, quiet_log):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    _apply_auth_mode(_settings("subscription"), quiet_log)
    call_args = quiet_log.info.call_args
    # First positional is the event name; we want the scrub flag in kwargs.
    assert call_args.args[0] == "auth.subscription_mode_active"
    assert call_args.kwargs.get("scrubbed_api_key") is True


def test_subscription_mode_silent_when_no_key(isolated_env, quiet_log):
    _apply_auth_mode(_settings("subscription"), quiet_log)
    call_args = quiet_log.info.call_args
    assert call_args.args[0] == "auth.subscription_mode_active"
    assert "scrubbed_api_key" not in call_args.kwargs


def test_api_key_mode_keeps_key(monkeypatch, quiet_log):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    _apply_auth_mode(_settings("api_key"), quiet_log)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-key"


def test_api_key_mode_raises_when_missing(isolated_env, quiet_log):
    with pytest.raises(SystemExit) as exc:
        _apply_auth_mode(_settings("api_key"), quiet_log)
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    assert "subscription" in str(exc.value)  # error mentions the fix


def test_auto_mode_with_key_keeps_it(monkeypatch, quiet_log):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    _apply_auth_mode(_settings("auto"), quiet_log)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-key"
    quiet_log.info.assert_called_with("auth.auto_mode", picked="api_key")


def test_auto_mode_without_key_logs_subscription(isolated_env, quiet_log):
    _apply_auth_mode(_settings("auto"), quiet_log)
    quiet_log.info.assert_called_with("auth.auto_mode", picked="subscription")
