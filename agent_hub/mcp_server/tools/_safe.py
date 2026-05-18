"""Decorator that wraps MCP tool functions to catch common errors and
return them as {"error": ...} dicts instead of raising. Without this
wrapper, an exception in a tool becomes a generic MCP protocol error
that loses the original message — making it hard for the calling
agent to self-correct on the next turn.
"""

from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable

import aiosqlite

from agent_hub.state_machine import InvalidTransition


def safe_tool(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except InvalidTransition as exc:
            return {"error": str(exc)}
        except aiosqlite.IntegrityError as exc:
            return {"error": f"DB integrity error: {exc}"}
        except (ValueError, KeyError) as exc:
            return {"error": str(exc)}
    return wrapper
