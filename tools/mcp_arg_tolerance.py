"""Accept a harmless payload key a host passes beside ``script``.

``goal_reached`` is a field of the action-script JSON. A host that passes it as
a top-level tool argument got the whole call rejected (measured: a rejected final
call cost ~5 s). This drops the key before validation; a true value is kept by
moving it into the ``script`` object when that has none.
"""

from __future__ import annotations

import json
from typing import Any

from config.settings_guard import never_raise

TOLERATED = ("goal_reached",)


def _declared(tool: Any) -> set[str]:
    schema = getattr(tool, "parameters", None) or {}
    return {str(k) for k in (schema.get("properties") or {})}


def _into_script(args: dict[str, Any], key: str, value: Any) -> None:
    """Carry a true *key* into the ``script`` JSON object, else forget it."""
    if value is not True:
        return
    with never_raise():
        script = json.loads(str(args.get("script") or ""))
        if isinstance(script, dict) and key not in script:
            script[key] = True
            args["script"] = json.dumps(script)


async def tolerate(context: Any) -> None:
    """Strip tolerated, undeclared top-level keys from the call in *context*."""
    with never_raise():
        message = context.message
        args = message.arguments
        keys = [k for k in TOLERATED if isinstance(args, dict) and k in args]
        if not keys:
            return
        fm = getattr(getattr(context, "fastmcp_context", None), "fastmcp", None)
        tool = await fm.get_tool(str(message.name)) if fm is not None else None
        for key in keys:
            if key not in _declared(tool):
                _into_script(args, key, args.pop(key))
