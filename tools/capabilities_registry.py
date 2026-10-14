"""Canonical enumeration of a FastMCP server's tools and prompts."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Iterable, Mapping

# --------------------------------------------------------- enumeration ----- #


# The helper this replaced (``tests/test_api_test_agent._list_tools``) tried
# ``srv._tool_manager.list_tools()`` FIRST. That branch is deliberately absent
# here: it is a private attribute, no fastmcp version reached in this tree
# resolves through it, and the two public shapes below cover 2.x and 3.x. A
# private fallback that never fires is a third shape to keep in step for no
# gain -- and three hand-mirrored copies of this ladder is the drift this
# module exists to end.
async def _registry_async(server: Any, methods: Iterable[str], attr: str) -> dict:
    """``{name: object}`` for one registry, across every fastmcp shape.

    ``FastMCP.get_tools`` is a *coroutine function* in fastmcp 2.14.7
    (measured), so a caller that forgets to await it compares a coroutine
    object against a dict, enumerates nothing, and passes. 3.x replaces it with
    ``list_tools()`` returning a list. The fakes in this repo's tests expose a
    plain ``.tools`` dict and no method at all, which is the third shape.
    """
    for name in methods:
        fn = getattr(server, name, None)
        if fn is None:
            continue
        res = fn()
        if inspect.isawaitable(res):
            res = await res
        return _as_mapping(res)
    return _as_mapping(getattr(server, attr, {}))


def _as_mapping(res: Any) -> dict:
    """A registry return of any shape as ``{name: object}``."""
    if isinstance(res, Mapping):
        return dict(res)
    return {getattr(item, "name", str(item)): item for item in res or ()}


def _registry(server: Any, methods: Iterable[str], attr: str) -> dict:
    """Sync bridge. Raises if a loop is already running -- use the async form."""
    return asyncio.run(_registry_async(server, methods, attr))


async def tools_by_name(server: Any) -> dict:
    """``{tool_name: Tool}`` -- the canonical async enumeration."""
    return await _registry_async(server, ("get_tools", "list_tools"), "tools")


async def prompts_by_name(server: Any) -> dict:
    """``{prompt_name: Prompt}`` -- the canonical async enumeration."""
    return await _registry_async(server, ("get_prompts", "list_prompts"), "prompts")


def tool_names(server: Any) -> list:
    """Sorted registered tool names. Sync; not for use inside a running loop."""
    return sorted(_registry(server, ("get_tools", "list_tools"), "tools"))


def prompt_names(server: Any) -> list:
    """Sorted registered prompt names. Sync; not for use inside a running loop."""
    return sorted(_registry(server, ("get_prompts", "list_prompts"), "prompts"))
