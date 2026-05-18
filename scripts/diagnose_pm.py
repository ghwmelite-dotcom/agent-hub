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

    # Seed a task so PM has something concrete to act on. This mirrors
    # what the smoke test does (we pre-create the task, then send PM a
    # handoff message that references it).
    task = await repo.create(
        title="Add hello line to README",
        description=(
            "Append a single line `Hello agent team.` to README.md "
            "in this repo. This is a diagnostic - keep the change minimal."
        ),
        origin_chat_id=999,
    )
    print(f"=== seeded task #{task.id}", flush=True)

    registry = AgentRegistry.load()
    pm_role = registry.get("pm")
    print(f"=== PM role model: {pm_role.model}", flush=True)
    print(f"=== PM allowed_tools ({len(pm_role.allowed_tools)}):", flush=True)
    for t in pm_role.allowed_tools:
        print(f"    {t}", flush=True)
    print(f"=== PM system_prompt length: {len(pm_role.system_prompt)} chars", flush=True)

    runner = AgentRunner(settings=settings, registry=registry)
    routed = (
        f"[task #{task.id}, from @user] "
        f"User filed task #{task.id}: append 'Hello agent team.' to README.md"
    )
    print(f"\n=== sending to PM:\n{routed}\n", flush=True)
    print("=== events:", flush=True)

    try:
        async for event in runner.send("pm", routed, task_id=task.id):
            if isinstance(event, TextChunk):
                print(f"[TEXT] {event.text!r}", flush=True)
            elif isinstance(event, ToolStart):
                tool = event.tool
                inp = repr(event.input)[:200]
                print(f"[TOOL_START] {tool}  input={inp}", flush=True)
            elif isinstance(event, ToolEnd):
                marker = "ERR" if event.is_error else "OK"
                print(f"[TOOL_END/{marker}] tool={event.tool!r}", flush=True)
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

    # Did PM update the task status?
    final = await repo.get(task.id)
    print(f"=== task #{task.id} final status: {final.status.value}", flush=True)
    print(f"=== task #{task.id} owner: {final.owner}", flush=True)

    # Recent events on the task (status_change, comments) — did PM call tasks.update / tasks.comment?
    events = await repo.events(task.id)
    print(f"=== task #{task.id} events ({len(events)}):", flush=True)
    for ev in events:
        body_repr = repr(ev.payload)[:120]
        print(f"    {ev.actor} {ev.kind}: {body_repr}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
