"""Pure handler for /budget — view or set the cumulative spend cap.

Three forms:
- `/budget`           — show current cap + total spent so far
- `/budget <amount>`  — set the cap (e.g. `/budget 5.00`)
- `/budget off`       — disable the cap

Cap is persisted in settings_kv under "budget_cap_usd". Total spend
is the sum of `tasks.cost_usd_total` accumulated by `_tick_handoff`.
The orchestrator's handoff loop reads the cap on every claim and
pauses dispatch (without blocking tasks) when the cap is exceeded.
"""

from __future__ import annotations

from pathlib import Path

from agent_hub.db import Database
from agent_hub.tasks.repository import TaskRepository


_BUDGET_KEY = "budget_cap_usd"


async def get_budget_cap(db_path: Path) -> float | None:
    """Returns the current cap, or None if no cap is set."""
    db = Database(db_path)
    raw = await db.get_kv(_BUDGET_KEY)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def set_budget_cap(db_path: Path, cap_usd: float) -> None:
    db = Database(db_path)
    await db.set_kv(_BUDGET_KEY, str(cap_usd))


async def clear_budget_cap(db_path: Path) -> None:
    """Persist a sentinel that disables the cap. Read as None."""
    db = Database(db_path)
    # Setting empty string is treated as "no cap" by get_budget_cap's
    # float() fallback below (raises → returns None).
    await db.set_kv(_BUDGET_KEY, "")


async def handle_budget(*, args: list[str], db_path: Path) -> str:
    """Render the /budget reply."""
    repo = TaskRepository(db_path)
    spent = await repo.total_cost_usd()

    if not args:
        cap = await get_budget_cap(db_path)
        cap_line = (
            f"Cap: ${cap:.2f}" if cap is not None else "Cap: (none — unlimited)"
        )
        headroom = ""
        if cap is not None:
            remaining = cap - spent
            if remaining < 0:
                headroom = f"  ⚠️ over by ${-remaining:.2f}"
            else:
                headroom = f"  (${remaining:.2f} remaining)"
        return f"💰 {cap_line}\nSpent so far: ${spent:.4f}{headroom}"

    arg = args[0].lower().strip()
    if arg in {"off", "none", "unlimited", "remove", "clear"}:
        await clear_budget_cap(db_path)
        return f"💰 Budget cap removed. Spent so far: ${spent:.4f}"

    try:
        cap = float(args[0].lstrip("$"))
    except ValueError:
        return (
            "Usage: /budget         — show current cap + spend\n"
            "       /budget 5.00    — set cap to $5.00\n"
            "       /budget off     — remove cap"
        )

    if cap <= 0:
        return "Cap must be positive. Use `/budget off` to remove the cap entirely."

    await set_budget_cap(db_path, cap)
    return f"💰 Budget cap set to ${cap:.2f}. Spent so far: ${spent:.4f}"
