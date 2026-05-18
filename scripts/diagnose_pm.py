"""One-shot diagnostic: invoke PM once with a real task context and
print every event with full detail.

Purpose: determine whether PM is actually calling MCP tools or just
emitting text. The orchestrator's handoff loop suppresses ToolStart
events so we're blind to what's happening at the SDK level.

Usage:
    .\\.venv\\Scripts\\python.exe -m scripts.diagnose_pm
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Force UTF-8 stdout on Windows.
for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


async def main() -> int:
    # Late imports so the print-config above takes effect.
    from agent_hub.agents import AgentRegistry, AgentRunner
    from agent_hub.agents.runner import (
        AgentError, TextChunk, ToolEnd, ToolStart, TurnDone,
    )
    from agent_hub.config import Settings
    from agent_hub.db import Database
    from agent_hub.tasks.repository import TaskRepository

    tmp = Path(tempfile.mkdtemp(prefix="diagnose_pm_"))
    print(f"=== tmp dir: {tmp}", flush=True)

    db_path = tmp / "agent_hub.db"
    os.environ["AGENT_HUB_DB"] = str(db_path)

    settings = Settings(
        telegram_bot_token="diag-dummy",
        telegram_allowed_user_id=1,
        database_path=db_path,
        agent_workspaces=[tmp],
    )

    db = Database(db_path)
    await db.init()
    repo = TaskRepository(db_path)

    # Simulate the REAL user-message flow:
    # User types "@pm append 'Hello' to README" in Telegram → router strips
    # the @pm and calls runner.send("pm", raw_text). No pre-created task,
    # no routing prefix. PM should call mcp__agent_hub__tasks_create first.
    # Seed a README so the agent has somewhere to work even if it tries
    # to inspect the project before handing off.
    (tmp / "README.md").write_text("# Smoke project\n\nA tiny test target.\n")
    print("=== seeded README.md in workspace", flush=True)

    registry = AgentRegistry.load()
    pm_role = registry.get("pm")
    print(f"=== PM role model: {pm_role.model}", flush=True)
    print(f"=== PM allowed_tools ({len(pm_role.allowed_tools)}):", flush=True)
    for t in pm_role.allowed_tools:
        print(f"    {t}", flush=True)
    print(f"=== PM system_prompt length: {len(pm_role.system_prompt)} chars", flush=True)

    runner = AgentRunner(settings=settings, registry=registry)
    # Realistic user-message: route prefix carries chat_id so PM can
    # pass origin_chat_id to mcp__agent_hub__tasks_create. The production
    # router needs to do this same prepend (currently doesn't — TODO).
    user_message = (
        "[chat_id=12345] Append the line 'Hello agent team.' to README.md."
    )
    print(f"\n=== sending to PM (no task_id, no prefix):\n{user_message}\n", flush=True)
    print("=== events:", flush=True)

    try:
        async for event in runner.send("pm", user_message, task_id=None):
            if isinstance(event, TextChunk):
                print(f"[TEXT] {event.text!r}", flush=True)
            elif isinstance(event, ToolStart):
                tool = event.tool
                # Print FULL input — truncation was hiding the bug.
                print(f"[TOOL_START] {tool}  input={event.input!r}", flush=True)
            elif isinstance(event, ToolEnd):
                marker = "ERR" if event.is_error else "OK"
                # Try to dig out the underlying tool result message
                # (SDK may attach it as a `content` or `output` attribute).
                extra = ""
                for attr in ("content", "output", "result", "message"):
                    val = getattr(event, attr, None)
                    if val:
                        extra = f"  {attr}={val!r}"
                        break
                print(f"[TOOL_END/{marker}] tool={event.tool!r}{extra}", flush=True)
            elif isinstance(event, TurnDone):
                cost = f"${event.cost_usd:.4f}" if event.cost_usd is not None else "?"
                dur = f"{event.duration_ms}ms" if event.duration_ms is not None else "?"
                print(f"[TURN_DONE] cost={cost} duration={dur}", flush=True)
            elif isinstance(event, AgentError):
                print(f"[AGENT_ERROR] {event.message}", flush=True)
            else:
                print(f"[OTHER] {type(event).__name__}: {event!r}", flush=True)
    finally:
        await runner.shutdown()

    # After the turn, did PM enqueue any handoffs?
    from agent_hub.tasks.handoff_queue import HandoffQueue
    queue = HandoffQueue(db_path)
    pending = await queue.pending()
    print(f"\n=== pending handoffs after PM turn: {len(pending)}", flush=True)
    for h in pending:
        print(f"    to={h.to_agent} from={h.from_agent} msg={h.message[:80]!r}", flush=True)

    # Did PM call tasks.create? If so, find the new task and dump its state.
    all_tasks = await repo.list()
    print(f"=== tasks in DB after PM turn: {len(all_tasks)}", flush=True)
    for t in all_tasks:
        print(f"    #{t.id} status={t.status.value} owner={t.owner} title={t.title!r}", flush=True)
        events = await repo.events(t.id)
        for ev in events:
            body_repr = repr(ev.payload)[:120]
            print(f"      event @{ev.actor} {ev.kind}: {body_repr}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
