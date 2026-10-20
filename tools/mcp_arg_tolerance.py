"""Accept invented top-level arguments instead of rejecting the call.

A chat model sends keys no tool declares (``tester_inputs``, ``sub_goal``,
``app``, ``goal_reached``...). Each rejected call cost a round trip. The key is
dropped before validation and a one-line note naming it (never its value) is
added to the reply. ``goal_reached``, ``request_extension``,
``extension_reason``, ``finding`` and ``sub_goal`` are fields of the
action-script JSON: a given value is moved into the ``script`` object when that
has none, and the note says it was moved.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp.types import TextContent

from config.settings_guard import never_raise
from tools import mcp_arg_refuse

try:
    from fastmcp.exceptions import ToolError
except ImportError:  # pragma: no cover - older framework
    ToolError = RuntimeError  # type: ignore[misc,assignment]

logger = logging.getLogger("qa_agents.mcp")

TOLERATED = (
    "goal_reached",
    "request_extension",
    "extension_reason",
    "finding",
    "sub_goal",
)
MOVED = "moved:"
MAX_NOTED = 8


def _declared(tool: Any) -> set[str]:
    schema = getattr(tool, "parameters", None) or {}
    return {str(k) for k in (schema.get("properties") or {})}


def _given(value: Any) -> bool:
    """False for the empty values a tolerated flag carries by default."""
    return not (value is None or value is False or value == "")


def _into_script(args: dict[str, Any], key: str, value: Any) -> bool:
    """Carry *key* into the ``script`` JSON object; True when it was moved."""
    if not _given(value):
        return False
    with never_raise():
        script = json.loads(str(args.get("script") or ""))
        if isinstance(script, dict) and key not in script:
            script[key] = value
            args["script"] = json.dumps(script)
            return True
    return False


def _safe(name: object) -> str:
    return "".join(c for c in str(name) if c.isalnum() or c in "_-.")[:40] or "?"


async def _tool_of(context: Any) -> Any:
    fm = getattr(getattr(context, "fastmcp_context", None), "fastmcp", None)
    if fm is None:
        return None
    return await fm.get_tool(str(context.message.name))


def _sort_keys(args: dict[str, Any], declared: set[str]) -> tuple[list[str], dict]:
    """Drop or collect every undeclared key: ``(dropped names, refused map)``."""
    dropped: list[str] = []
    refused: dict[str, str] = {}
    for key in [k for k in args if k not in declared]:
        close = None if key in TOLERATED else mcp_arg_refuse.closest(key, declared)
        if close is not None:
            refused[_safe(key)] = close
            continue
        value = args.pop(key)
        if key in TOLERATED and _into_script(args, key, value):
            dropped.append(MOVED + _safe(key))
        elif key not in TOLERATED or _given(value):
            dropped.append(_safe(key))
    return dropped, refused


async def tolerate(context: Any) -> list[str]:
    """Drop undeclared keys (names sorted); REFUSE safety-looking ones."""
    dropped: list[str] = []
    refused: dict[str, str] = {}
    with never_raise():
        args = context.message.arguments
        declared = _declared(await _tool_of(context))
        if isinstance(args, dict) and declared:
            dropped, refused = _sort_keys(args, declared)
    if dropped or refused:
        logger.warning(
            "tool args dropped=%s refused=%s", sorted(dropped), sorted(refused)
        )
    if refused:
        raise ToolError(mcp_arg_refuse.refusal(refused))
    return sorted(dropped)


def note_for(dropped: list[str]) -> str:
    """The one-line reply note naming the ignored arguments."""
    moved = [d[len(MOVED) :] for d in dropped if d.startswith(MOVED)]
    gone = [d for d in dropped if not d.startswith(MOVED)]
    notes = []
    if gone:
        notes.append(f"ignored argument(s) this tool does not take: {_names(gone)}.")
    if moved:
        notes.append(f"moved {_names(moved)} into the script JSON; put it there.")
    return "Note: " + " ".join(notes)


def _names(names: list[str]) -> str:
    return ", ".join(names[:MAX_NOTED])


def add_note(result: Any, dropped: list[str]) -> Any:
    """Append the note to a tool result's text content; the result otherwise."""
    with never_raise():
        result.content.append(TextContent(type="text", text=note_for(dropped)))
    return result
