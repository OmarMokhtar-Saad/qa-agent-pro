"""Text assembly for the injected knowledge blocks (see ``knowledge_inject``)."""

from __future__ import annotations

from typing import Any

from tools.mobile import knowledge_limits as limits
from tools.untrusted import wrap_untrusted


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return 99.0


def _line(text: Any, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap]


def sort_key(item: dict) -> tuple:
    return (
        _num(item.get("prio")),
        -_num(item.get("net")) if item.get("net") is not None else 0.0,
        str(item.get("table") or ""),
        _num(item.get("num")) if item.get("num") is not None else 0.0,
        str(item.get("id") or ""),
    )


def take(got: Any, seen: set) -> list:
    """The usable, not yet seen items of one source's answer."""
    out: list = []
    for item in got:
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            continue
        ident = str(item.get("id") or "")
        if ident in seen:
            continue
        seen.add(ident)
        out.append(item)
    return out


def stable_text(items: list) -> str:
    lines: list = []
    budget = limits.STABLE_BLOCK_TOKENS * 4
    for item in items:
        line = "- " + _line(item.get("text"), limits.STABLE_BLOCK_CHARS)
        if budget - len(line) < 0:
            break
        budget -= len(line) + 1
        lines.append(line)
    if not lines:
        return ""
    return wrap_untrusted(
        "app_knowledge", "\n".join(lines), limit=limits.STABLE_BLOCK_CHARS
    )


def screen_text(items: list) -> str:
    lines = [
        "- " + _line(item.get("text"), limits.RULE_CHARS)
        for item in items[: limits.MAX_TURN_RULES]
    ]
    if not lines:
        return ""
    return wrap_untrusted(
        "app_knowledge_screen",
        "\n".join(lines),
        limit=limits.MAX_TURN_RULES * (limits.RULE_CHARS + 4),
    )
