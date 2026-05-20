"""Telegram bot — handlers and application factory."""

from __future__ import annotations

from pathlib import Path

import structlog
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agent_hub.agents.runner import (
    AgentError,
    AgentEvent,
    TextChunk,
    ToolEnd,
    ToolStart,
    TurnDone,
)
from agent_hub.config import Settings
from agent_hub.orchestrator import Orchestrator
from agent_hub.telegram_bot.streamer import StreamingMessage

log = structlog.get_logger(__name__)


# ----------------------------------------------------------------------
# Auth — bot ignores anyone who isn't the configured user.
# ----------------------------------------------------------------------


def _is_authorized(update: Update, allowed_user_id: int) -> bool:
    if update.effective_user is None:
        return False
    return update.effective_user.id == allowed_user_id


async def _reject(update: Update) -> None:
    user_id = update.effective_user.id if update.effective_user else "?"
    log.warning("bot.unauthorized", user_id=user_id)
    if update.message:
        await update.message.reply_text(
            "This bot is private. If you're its owner, set your Telegram user "
            f"ID as TELEGRAM_ALLOWED_USER_ID in .env. Your ID is: {user_id}"
        )


# ----------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings.telegram_allowed_user_id):
        await _reject(update)
        return

    orch: Orchestrator = context.application.bot_data["orchestrator"]
    workspace = orch.runner.workspace
    workspace_line = f"Workspace: {workspace}" if workspace else "No workspace set."

    team_lines = []
    for role in orch.registry.all():
        team_lines.append(f"• {role.display_name} (`@{role.name}`)")

    text = (
        "Agent Hub is online.\n\n"
        f"{workspace_line}\n\n"
        "Your team:\n" + "\n".join(team_lines) + "\n\n"
        "Default replies go to the Senior PM. Address anyone with `@name`.\n"
        "Type /help for commands."
    )
    await update.message.reply_text(text)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings.telegram_allowed_user_id):
        await _reject(update)
        return

    await update.message.reply_text(
        "*Session*\n"
        "/start — show team + workspace\n"
        "/agents — list the team\n"
        "/to <agent> — set who you're talking to (sticky)\n"
        "/reset <agent|all> — clear an agent's memory\n"
        "/workspace [path] — show or change the project folder\n"
        "/projects — recently-used project folders\n"
        "/whoami — your Telegram user ID + version\n\n"
        "*Tasks*\n"
        "/tasks — list active tasks\n"
        "/task <id> — show one task in detail\n"
        "/approve <id> — approve a design gate (worktree + handoff)\n"
        "/reject <id> <reason> — reject a design with feedback\n"
        "/cancel <id> — abort a running task\n"
        "/resume <id> — resume a stale/blocked task\n"
        "/status — orchestrator health snapshot\n"
        "/budget [amount|off] — view, set, or disable the spend cap\n\n"
        "Address an agent directly with `@name` (e.g. `@architect`, `@impl`)."
    )


async def cmd_agents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings.telegram_allowed_user_id):
        await _reject(update)
        return

    orch: Orchestrator = context.application.bot_data["orchestrator"]
    lines = []
    for role in orch.registry.all():
        aliases = ", ".join(f"@{a}" for a in [role.name, *role.aliases])
        lines.append(f"• {role.display_name} — {aliases}")
    await update.message.reply_text("Team:\n" + "\n".join(lines))


async def cmd_to(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings.telegram_allowed_user_id):
        await _reject(update)
        return

    orch: Orchestrator = context.application.bot_data["orchestrator"]
    args = context.args or []
    if not args:
        current = orch.sticky_for(update.effective_chat.id) or "(default → PM)"
        await update.message.reply_text(f"Currently talking to: {current}")
        return

    target = args[0].lstrip("@")
    canonical = orch.registry.resolve(target)
    if not canonical:
        await update.message.reply_text(f"Unknown agent: {target}")
        return
    orch.set_sticky(update.effective_chat.id, canonical)
    role = orch.registry.get(canonical)
    await update.message.reply_text(f"Switched to {role.display_name}.")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings.telegram_allowed_user_id):
        await _reject(update)
        return

    orch: Orchestrator = context.application.bot_data["orchestrator"]
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /reset <agent|all>")
        return

    target = args[0].lstrip("@").lower()
    if target == "all":
        for role in orch.registry.all():
            await orch.runner.reset(role.name)
        orch.clear_sticky(update.effective_chat.id)
        await update.message.reply_text("All agents reset.")
        return

    canonical = orch.registry.resolve(target)
    if not canonical:
        await update.message.reply_text(f"Unknown agent: {target}")
        return
    await orch.runner.reset(canonical)
    await update.message.reply_text(
        f"{orch.registry.get(canonical).display_name} reset."
    )


async def cmd_workspace(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings.telegram_allowed_user_id):
        await _reject(update)
        return

    orch: Orchestrator = context.application.bot_data["orchestrator"]
    args = context.args or []
    if not args:
        current = orch.runner.workspace
        mode_hint = f" ({settings.workspace_mode} mode)"
        await update.message.reply_text(
            f"Workspace: {current}{mode_hint}" if current else f"No workspace set{mode_hint}."
        )
        return

    new_path = Path(" ".join(args)).expanduser()
    if not new_path.is_dir():
        await update.message.reply_text(f"Not a directory: {new_path}")
        return

    # Enforce the allowlist only if we're in allowlist mode.
    if settings.workspace_mode == "allowlist":
        allowed = settings.agent_workspaces
        if allowed and not any(_is_within(new_path, root) for root in allowed):
            await update.message.reply_text(
                "That path isn't in AGENT_WORKSPACES. Edit .env to allow it, "
                "or set AGENT_WORKSPACE_MODE=open."
            )
            return

    await _switch_workspace(orch, new_path)
    await update.message.reply_text(
        f"Workspace set to {new_path}. All agents reset to pick it up."
    )


async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings.telegram_allowed_user_id):
        await _reject(update)
        return

    orch: Orchestrator = context.application.bot_data["orchestrator"]
    recent = await orch.db.list_recent_workspaces()
    current = str(orch.runner.workspace) if orch.runner.workspace else None

    if not recent:
        await update.message.reply_text(
            "No recent projects yet.\n"
            "Use /workspace <path> to start working in one — e.g. "
            "`/workspace C:\\dev\\baobab`."
        )
        return

    lines = ["Recent projects (newest first):"]
    for path in recent:
        marker = "→" if path == current else " "
        lines.append(f"{marker} `{path}`")
    lines.append("\nSwitch with `/workspace <path>`.")
    await update.message.reply_text("\n".join(lines))


async def _switch_workspace(orch: Orchestrator, new_path: Path) -> None:
    """Apply a new workspace: set on runner, persist to DB, reset agents."""
    orch.runner.set_workspace(new_path)
    await orch.db.set_active_workspace(str(new_path))
    # Existing agents keep their old cwd — reset to pick up the new one.
    for role in orch.registry.all():
        await orch.runner.reset(role.name)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    from agent_hub import __version__

    await update.message.reply_text(
        f"User ID: {user.id if user else '?'}\n"
        f"Chat ID: {chat.id if chat else '?'}\n"
        f"Agent Hub v{__version__}"
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings.telegram_allowed_user_id):
        await _reject(update)
        return

    if not update.message or not update.message.text:
        return

    orch: Orchestrator = context.application.bot_data["orchestrator"]
    chat_id = update.effective_chat.id
    text = update.message.text

    # Show "typing…" while we work.
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    stream: StreamingMessage | None = None
    current_display: str | None = None

    try:
        async for display_name, event in orch.handle(chat_id=chat_id, message=text):
            if stream is None:
                current_display = display_name
                stream = StreamingMessage(
                    chat_id=chat_id,
                    bot=context.bot,
                    prefix=f"*{display_name}*\n",
                )

            await _render_event(stream, event)

        if stream is not None:
            await stream.finalize()
    except Exception as exc:  # noqa: BLE001
        log.exception("bot.message_failed")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Sorry — something broke handling that message:\n`{exc}`",
        )


async def _render_event(stream: StreamingMessage, event: AgentEvent) -> None:
    if isinstance(event, TextChunk):
        await stream.append(event.text)
    elif isinstance(event, ToolStart):
        # Short inline indicator so the user sees activity.
        hint = _summarize_tool(event.tool, event.input)
        await stream.append(f"\n_{hint}_\n")
    elif isinstance(event, ToolEnd):
        if event.is_error:
            await stream.append("\n_(tool failed)_\n")
    elif isinstance(event, AgentError):
        await stream.append(f"\n⚠️ {event.message}\n")
    elif isinstance(event, TurnDone):
        pass  # Could surface cost/time here later.


def _summarize_tool(tool: str, args: dict) -> str:
    """Compact one-line description of a tool invocation for streaming UI."""
    if tool in {"Read", "Edit", "Write"}:
        path = args.get("file_path") or args.get("path") or "?"
        return f"{tool} {path}"
    if tool == "Bash":
        cmd = (args.get("command") or "").strip().splitlines()[0:1]
        return f"$ {cmd[0] if cmd else ''}"[:80]
    if tool in {"Grep", "Glob"}:
        pat = args.get("pattern") or args.get("query") or "?"
        return f"{tool} {pat}"
    if tool in {"WebSearch", "WebFetch"}:
        q = args.get("query") or args.get("url") or "?"
        return f"{tool} {q}"
    return tool


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


# ----------------------------------------------------------------------
# Application factory
# ----------------------------------------------------------------------


def build_application(
    *,
    settings: Settings,
    orchestrator: Orchestrator,
) -> Application:
    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .build()
    )

    app.bot_data["settings"] = settings
    app.bot_data["orchestrator"] = orchestrator

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("agents", cmd_agents))
    app.add_handler(CommandHandler("to", cmd_to))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("workspace", cmd_workspace))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("whoami", cmd_whoami))

    # ------------------------------------------------------------------
    # Task-management commands (Tasks 9-13)
    # ------------------------------------------------------------------
    from agent_hub.telegram_bot.commands.approve_cmd import handle_approve
    from agent_hub.telegram_bot.commands.budget_cmd import handle_budget
    from agent_hub.telegram_bot.commands.cancel_cmd import handle_cancel
    from agent_hub.telegram_bot.commands.reject_cmd import handle_reject
    from agent_hub.telegram_bot.commands.status_cmd import handle_status
    from agent_hub.telegram_bot.commands.tasks_cmd import handle_tasks
    from agent_hub.telegram_bot.commands.task_cmd import handle_task
    from agent_hub.telegram_bot.commands.resume_cmd import handle_resume

    db_path = settings.database_path

    async def _on_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        reply = await handle_tasks(db_path=db_path)
        await update.effective_chat.send_message(reply)

    async def _on_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.effective_chat.send_message("Usage: /task <id>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.effective_chat.send_message("Task id must be an integer.")
            return
        reply = await handle_task(task_id=task_id, db_path=db_path)
        await update.effective_chat.send_message(reply)

    async def _on_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.effective_chat.send_message("Usage: /approve <id>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.effective_chat.send_message("Task id must be an integer.")
            return

        # Pull the LIVE workspace from the runner, not the static env value.
        # User may have switched via /workspace <path>.
        repo_root = orchestrator.runner.workspace

        if repo_root is None:
            await update.effective_chat.send_message(
                "Cannot approve — no workspace is configured. "
                "Set AGENT_WORKSPACES in .env or use /workspace <path> first."
            )
            return

        worktrees_root = repo_root.parent / "worktrees"

        reply = await handle_approve(
            task_id=task_id,
            db_path=db_path,
            repo_root=repo_root,
            worktrees_root=worktrees_root,
        )
        await update.effective_chat.send_message(reply)

    async def _on_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if len(context.args) < 2:
            await update.effective_chat.send_message("Usage: /reject <id> <reason>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.effective_chat.send_message("Task id must be an integer.")
            return
        reason = " ".join(context.args[1:])
        workspace = orchestrator.runner.workspace
        reply = await handle_reject(
            task_id=task_id,
            reason=reason,
            db_path=db_path,
            workspace=str(workspace) if workspace else None,
        )
        await update.effective_chat.send_message(reply)

    async def _on_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        reply = await handle_status(db_path=db_path, runner=orchestrator.runner)
        await update.effective_chat.send_message(reply)

    async def _on_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        reply = await handle_budget(args=context.args or [], db_path=db_path)
        await update.effective_chat.send_message(reply)

    async def _on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.effective_chat.send_message("Usage: /cancel <id>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.effective_chat.send_message("Task id must be an integer.")
            return
        reply = await handle_cancel(
            task_id=task_id,
            db_path=db_path,
            runner=orchestrator.runner,
        )
        await update.effective_chat.send_message(reply)

    async def _on_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.effective_chat.send_message("Usage: /resume <id>")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.effective_chat.send_message("Task id must be an integer.")
            return
        reply = await handle_resume(task_id=task_id, db_path=db_path)
        await update.effective_chat.send_message(reply)

    app.add_handler(CommandHandler("tasks", _on_tasks))
    app.add_handler(CommandHandler("task", _on_task))
    app.add_handler(CommandHandler("approve", _on_approve))
    app.add_handler(CommandHandler("reject", _on_reject))
    app.add_handler(CommandHandler("cancel", _on_cancel))
    app.add_handler(CommandHandler("resume", _on_resume))
    app.add_handler(CommandHandler("status", _on_status))
    app.add_handler(CommandHandler("budget", _on_budget))

    # Catch-all — must come AFTER all CommandHandlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    return app
