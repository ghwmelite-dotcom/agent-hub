"""Entry point: `python -m agent_hub`.

Loads settings, initializes the database, builds the agent runner and the
Telegram application, and runs the bot until Ctrl-C.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import structlog

from agent_hub.agents import AgentRegistry, AgentRunner
from agent_hub.config import Settings, load_settings
from agent_hub.db import Database
from agent_hub.orchestrator import Orchestrator
from agent_hub.orchestrator.lock import OrchestratorLock
from agent_hub.telegram_bot import build_application


def _resolve_lock_path(db_path: Path) -> Path:
    return db_path.parent / ".orchestrator.lock"


def _acquire_orchestrator_lock_or_exit(db_path: Path) -> OrchestratorLock:
    """Acquire the per-workspace orchestrator lock. Caller is responsible
    for releasing it on shutdown."""
    lock_path = _resolve_lock_path(db_path)
    lock = OrchestratorLock(lock_path)
    lock.acquire()
    return lock


def _export_db_path_to_env(db_path: Path) -> None:
    """Make the absolute DB path visible to child processes (e.g. the
    per-agent MCP servers) that inherit our environment."""
    os.environ["AGENT_HUB_DB"] = str(db_path)


def _build_orchestrator(
    *,
    settings: Settings,
    registry,
    runner,
    db: Database,
    surface,  # MessageSurface | None
):
    """Construct an Orchestrator wired to the correct repo_root."""
    return Orchestrator(
        registry=registry,
        runner=runner,
        db=db,
        surface=surface,
        repo_root=settings.default_workspace,
        handoff_worker_count=settings.handoff_worker_count,
        gate_reminder_hours=settings.gate_reminder_hours,
        stuck_turn_threshold=settings.stuck_turn_threshold,
    )


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    # Quiet down noisy libraries.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


async def _post_init(app, settings: Settings, runner: AgentRunner, db: Database, orchestrator) -> None:
    """Telegram PTB post-init hook. Initialize anything that needs an event loop."""
    log = structlog.get_logger("agent_hub")
    await db.init()

    # Restore the workspace the user was last in. Falls back to the .env
    # default, which AgentRunner already picked up at construction.
    saved = await db.get_active_workspace()
    if saved:
        saved_path = Path(saved)
        if saved_path.is_dir():
            runner.set_workspace(saved_path)
            log.info("workspace.restored", path=str(saved_path))
        else:
            log.warning("workspace.saved_missing", path=saved)
    elif runner.workspace:
        # First boot — seed recent list with the default workspace.
        await db.set_active_workspace(str(runner.workspace))

    # Install the real Telegram surface now that the app is available.
    from agent_hub.telegram_bot.surface_telegram import TelegramSurface
    orchestrator.surface = TelegramSurface(app)

    await orchestrator.start()

    # Restart-resume scan once at boot, after start so the loops are running.
    from agent_hub.orchestrator.resume import scan_stale_tasks
    await scan_stale_tasks(
        db_path=settings.database_path,
        surface=orchestrator.surface,
        released_claims_count=orchestrator.released_stale_claims,
    )


async def _post_shutdown(app, runner: AgentRunner, orchestrator) -> None:
    """Stop orchestrator and drain agent sessions cleanly on shutdown."""
    await orchestrator.stop()
    await runner.shutdown()


def main() -> None:
    settings = load_settings()
    _configure_logging(settings.log_level)

    log = structlog.get_logger("agent_hub")
    log.info(
        "agent_hub.starting",
        version=__import__("agent_hub").__version__,
        autonomy=settings.pm_autonomy,
        workspace=str(settings.default_workspace) if settings.default_workspace else None,
    )

    # Single-instance lock — refuses to start if another agent_hub is alive.
    lock = _acquire_orchestrator_lock_or_exit(settings.database_path)
    _export_db_path_to_env(settings.database_path)

    registry = AgentRegistry.load()
    runner = AgentRunner(settings=settings, registry=registry)
    db = Database(settings.database_path)

    orchestrator = _build_orchestrator(
        settings=settings,
        registry=registry,
        runner=runner,
        db=db,
        surface=None,
    )

    app = build_application(settings=settings, orchestrator=orchestrator)

    # PTB lets us hook into its init/shutdown lifecycle so we share its loop.
    app.post_init = lambda a: _post_init(a, settings, runner, db, orchestrator)
    app.post_shutdown = lambda a: _post_shutdown(a, runner, orchestrator)

    try:
        log.info("agent_hub.polling")
        # run_polling owns the event loop; blocks until SIGINT/SIGTERM.
        app.run_polling(stop_signals=None) if sys.platform == "win32" else app.run_polling()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
