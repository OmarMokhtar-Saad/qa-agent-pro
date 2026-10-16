"""A model-fixable error text for a tool call whose arguments failed validation.

The framework's own text is a pydantic dump the model cannot act on. This one
names each bad key, lists the keys the tool accepts and shows a corrected call.
NEVER a value: only argument names, error kinds and schema property names.
"""

from __future__ import annotations

from typing import Any

from config.settings_guard import never_raise

MAX_ROWS = 6
MAX_KEYS = 40
_KINDS = {
    "unexpected_keyword_argument": "is not an argument of this tool",
    "missing_argument": "is required but missing",
    "missing": "is required but missing",
}


def _rows(found: Any) -> list[tuple[str, str]]:
    """``(key, kind)`` per pydantic error, names only."""
    rows = found.errors(include_input=False, include_context=False, include_url=False)
    out: list[tuple[str, str]] = []
    for row in rows[:MAX_ROWS]:
        loc = row.get("loc") or ("?",)
        kind = _KINDS.get(str(row.get("type")), "has the wrong type or value")
        out.append((str(loc[0])[:40], kind))
    return out


def accepted_keys(tool: Any) -> tuple[list[str], list[str]]:
    """``(all keys, required keys)`` from a tool's JSON schema, else two empties."""
    with never_raise():
        schema = getattr(tool, "parameters", None) or {}
        keys = [str(k) for k in (schema.get("properties") or {})][:MAX_KEYS]
        return keys, [str(k) for k in schema.get("required") or []]
    return [], []


#: Turn-payload fields: a model that passes one as a tool argument is told
#: they ride inside the ``script`` JSON, not beside it.
_PAYLOAD_KEYS = frozenset({"sub_goal", "goal_reached", "request_extension"})


def _payload_hint(rows: list[tuple[str, str]], keys: list[str]) -> str:
    """One line when a rejected key belongs inside the ``script`` JSON."""
    bad = sorted(k for k, _ in rows if k in _PAYLOAD_KEYS)
    if "script" not in keys or not bad:
        return ""
    return f"`{'`, `'.join(bad)}` go INSIDE the `script` JSON, not as tool arguments."


def _example(name: str, required: list[str], keys: list[str]) -> str:
    """A call with the required keys, or the first accepted ones."""
    shown = required or keys[:2]
    return f"{name}(" + ", ".join(f'{k}="..."' for k in shown) + ")"


def fix_message(name: str, found: Any, tool: Any = None) -> str:
    """The error text for ``name``; a short generic one if the schema is unknown."""
    with never_raise():
        keys, required = accepted_keys(tool)
        rows = _rows(found)
        said = "; ".join(f"`{k}` {v}" for k, v in rows)
        lines = [f"{name} rejected: {said}."]
        if keys:
            lines.append("Accepted keys: " + ", ".join(keys) + ".")
            lines.append("Corrected example: " + _example(name, required, keys))
        hint = _payload_hint(rows, keys)
        if hint:
            lines.append(hint)
        return "\n".join(lines)
    return f"{name} rejected: invalid arguments."
