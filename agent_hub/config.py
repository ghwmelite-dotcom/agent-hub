"""Settings loaded from environment / .env file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

# Load .env once at import time. The bot's entry point also calls this
# explicitly to support test harnesses that import config without running main.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


Autonomy = Literal["low", "medium", "high"]
WorkspaceMode = Literal["allowlist", "open"]
AuthMode = Literal["subscription", "api_key", "auto"]


class Settings(BaseModel):
    """Runtime configuration. All values come from environment variables."""

    # Telegram
    telegram_bot_token: str = Field(..., description="From @BotFather")
    telegram_allowed_user_id: int = Field(
        ..., description="Your numeric Telegram user ID; bot ignores everyone else"
    )

    # Anthropic
    anthropic_api_key: str | None = None
    agent_default_model: str = "claude-sonnet-4-6"
    anthropic_auth_mode: AuthMode = "subscription"

    # Workspaces
    agent_workspaces: list[Path] = Field(default_factory=list)
    workspace_mode: WorkspaceMode = "open"

    # Behavior
    pm_autonomy: Autonomy = "medium"

    # Storage
    database_path: Path = Path("./data/agent_hub.db")
    log_level: str = "INFO"

    # Concurrency
    handoff_worker_count: int = Field(
        default=3,
        ge=1,
        description=(
            "Number of concurrent handoff-loop workers. Each runs claim → "
            "dispatch in its own coroutine, so multiple in-flight tasks "
            "progress in parallel. Per-(agent, task_id) ordering is "
            "protected by AgentRunner's client pool lock."
        ),
    )

    # Watchdogs
    gate_reminder_hours: float = Field(
        default=24.0,
        gt=0,
        description=(
            "Send a reminder DM about a still-pending design gate after "
            "this many hours, then every gate_reminder_hours thereafter."
        ),
    )
    stuck_turn_threshold: int = Field(
        default=12,
        gt=0,
        description=(
            "DM the user when a non-terminal task has had this many "
            "handoff turns since its last status_change. Default 12 = "
            "happy path (~5 turns) plus headroom for one round of "
            "reviewer kick-back."
        ),
    )

    # Dashboard
    dashboard_port: int = Field(
        default=8765,
        ge=0,
        description=(
            "Port for the bundled live dashboard (http://localhost:<port>). "
            "Set to 0 to disable."
        ),
    )

    # Computed
    project_root: Path = _PROJECT_ROOT

    @field_validator("agent_workspaces", mode="before")
    @classmethod
    def _parse_workspaces(cls, v: object) -> list[Path]:
        if isinstance(v, list):
            return [Path(p) for p in v]
        if isinstance(v, str):
            return [Path(p.strip()) for p in v.split(",") if p.strip()]
        return []

    @field_validator("database_path", mode="before")
    @classmethod
    def _abs_db_path(cls, v: object) -> Path:
        p = Path(str(v))
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        return p

    @property
    def default_workspace(self) -> Path | None:
        return self.agent_workspaces[0] if self.agent_workspaces else None


def load_settings() -> Settings:
    """Read environment variables and produce a validated Settings object."""
    return Settings(
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
        telegram_allowed_user_id=int(_required("TELEGRAM_ALLOWED_USER_ID")),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_auth_mode=os.getenv("ANTHROPIC_AUTH_MODE", "subscription"),  # type: ignore[arg-type]
        agent_default_model=os.getenv("AGENT_DEFAULT_MODEL", "claude-sonnet-4-6"),
        agent_workspaces=os.getenv("AGENT_WORKSPACES", ""),
        workspace_mode=os.getenv("AGENT_WORKSPACE_MODE", "open"),  # type: ignore[arg-type]
        pm_autonomy=os.getenv("PM_AUTONOMY", "medium"),  # type: ignore[arg-type]
        database_path=os.getenv("DATABASE_PATH", "./data/agent_hub.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        handoff_worker_count=int(os.getenv("HANDOFF_WORKER_COUNT", "3")),
        gate_reminder_hours=float(os.getenv("GATE_REMINDER_HOURS", "24")),
        stuck_turn_threshold=int(os.getenv("STUCK_TURN_THRESHOLD", "12")),
        dashboard_port=int(os.getenv("DASHBOARD_PORT", "8765")),
    )


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value
