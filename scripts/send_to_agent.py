"""Send a one-shot task to an agent and stream its response to stdout.

Bypasses the Telegram bot — useful for testing new agents, smoke-checking
prompts, or scripting tasks. Spawns its own AgentRunner, so it can be run
alongside a live agent_hub process without interfering.

Usage:
    python -m scripts.send_to_agent <agent> "<task>"
    python -m scripts.send_to_agent <agent> --stdin
    python -m scripts.send_to_agent <agent> "<task>" --workspace C:\\path\\to\\repo
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agent_hub.agents import AgentRegistry, AgentRunner
from agent_hub.agents.runner import (
    AgentError,
    TextChunk,
    ToolEnd,
    ToolStart,
    TurnDone,
)
from agent_hub.config import load_settings


async def run(agent: str, task: str, workspace: Path | None) -> int:
    # Windows defaults stdout to cp1252; agents routinely emit en-dashes,
    # arrows, smart quotes. Force UTF-8 so a single non-ASCII char doesn't
    # kill the stream mid-response.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    settings = load_settings()
    registry = AgentRegistry.load()

    canonical = registry.resolve(agent)
    if canonical is None:
        print(f"Unknown agent: {agent!r}", file=sys.stderr)
        print(f"Available: {', '.join(registry.names())}", file=sys.stderr)
        return 2

    runner = AgentRunner(settings=settings, registry=registry)
    if workspace is not None:
        runner.set_workspace(workspace)
    elif settings.default_workspace is None:
        print("No workspace set; pass --workspace or set AGENT_WORKSPACES.", file=sys.stderr)
        return 2

    role = registry.get(canonical)
    print(f"--- {role.display_name} ({role.model}) in {runner.workspace} ---", flush=True)

    try:
        async for event in runner.send(canonical, task):
            if isinstance(event, TextChunk):
                print(event.text, end="", flush=True)
            elif isinstance(event, ToolStart):
                print(f"\n  [tool] {event.tool} {_short_input(event.input)}", flush=True)
            elif isinstance(event, ToolEnd):
                marker = "err" if event.is_error else "ok"
                print(f"  [tool/{marker}]", flush=True)
            elif isinstance(event, TurnDone):
                cost = f"${event.cost_usd:.4f}" if event.cost_usd is not None else "?"
                dur = f"{event.duration_ms}ms" if event.duration_ms is not None else "?"
                print(f"\n--- done (cost={cost}, dur={dur}) ---", flush=True)
            elif isinstance(event, AgentError):
                print(f"\n--- ERROR: {event.message} ---", file=sys.stderr)
                return 1
    finally:
        await runner.shutdown()

    return 0


def _short_input(payload: dict) -> str:
    parts: list[str] = []
    for key, value in list(payload.items())[:3]:
        rendered = repr(value)
        if len(rendered) > 60:
            rendered = rendered[:57] + "..."
        parts.append(f"{key}={rendered}")
    return " ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent", help="Agent name or alias (e.g. 'fullstack', 'ux')")
    parser.add_argument("task", nargs="?", help="Task text. Omit and use --stdin to pipe.")
    parser.add_argument("--stdin", action="store_true", help="Read task from stdin")
    parser.add_argument("--workspace", type=Path, help="Override agent workspace cwd")
    args = parser.parse_args()

    if args.stdin:
        task = sys.stdin.read().strip()
    elif args.task:
        task = args.task
    else:
        parser.error("Provide a task argument or --stdin")
        return 2

    if not task:
        print("Empty task; nothing to send.", file=sys.stderr)
        return 2

    return asyncio.run(run(args.agent, task, args.workspace))


if __name__ == "__main__":
    raise SystemExit(main())
