"""Reply parsing of tools.host_llm (split out of host_llm.py)."""

from __future__ import annotations

from tools import host_llm_consts as hk
from tools.safe_parse import JsonMode, loads_json


def _cap(value: object, depth: int = 0) -> object:
    """Recursively cap strings, collection sizes and nesting depth. Never raises."""
    if depth > hk._MAX_DEPTH:
        return None
    if isinstance(value, str):
        return (
            value
            if len(value) <= hk._MAX_FIELD_CHARS
            else value[: hk._MAX_FIELD_CHARS] + "...[truncated]"
        )
    if isinstance(value, dict):
        out: dict = {}
        for key, item in list(value.items())[: hk._MAX_ITEMS]:
            out[str(key)[:200]] = _cap(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_cap(item, depth + 1) for item in list(value)[: hk._MAX_ITEMS]]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _cap(str(value), depth + 1)


def _loads(text: str) -> dict | None:
    """json.loads restricted to objects. Returns None on anything else."""
    obj = loads_json(text, JsonMode(dict_only=True, strip_both=True, swallow_all=True))
    return obj if isinstance(obj, dict) else None


def _fenced_object(text: str) -> dict | None:
    """First JSON object found in a fenced segment of *text*, else None."""
    for part in text.split("```"):
        candidate = part[4:] if part.lower().startswith("json") else part
        obj = _loads(candidate)
        if obj is not None:
            return obj
    return None


# Mirrors the host-mode submit path's tolerance: chat models wrap JSON in
# ```json fences or bracket it with prose. No eval, no regex backtracking --
# fenced segments first, then the whole string, then the outermost {...} span.
def _extract_json(raw: str) -> dict | None:
    """Tolerantly pull ONE JSON object out of a host reply (fenced or prose)."""
    text = (raw or "").strip()
    if not text:
        return None
    if "```" in text:
        obj = _fenced_object(text)
        if obj is not None:
            return obj
    obj = _loads(text)
    if obj is not None:
        return obj
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        return _loads(text[start : end + 1])
    return None
