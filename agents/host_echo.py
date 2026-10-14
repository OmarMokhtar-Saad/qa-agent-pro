"""Bounded, fence-safe echo of host-supplied identifiers (shared reply helpers).

THE INVARIANT: a host-supplied name reaches a reply only through a bounded,
fence-safe renderer, and the unrecognized-name list has exactly ONE renderer,
`_unrecognized_names_line` (a test asserts nothing else formats it).
"""

from __future__ import annotations

import logging
from typing import Any

from config.settings_guard import never_raise
from tools.untrusted import single_line

logger = logging.getLogger("agents.host_mode")

# How many unrecognized names a reply lists before summarising the rest.
_UNREC_SHOWN = 8


def _raw_length(value: object) -> int | None:
    """len(str(value or "")), or None when the value's __str__ raises."""
    # pragma: no cover - a hostile __str__
    with never_raise():
        return len(str(value or ""))
    return None


def clip_echo(value: object, limit: int = 120) -> str:
    """A caller-supplied identifier, bounded so it is safe to render back.

    Length-bounded, fence- and newline-safe via `single_line`; adds a
    truncation note so the tester sees the value was long, not wrong.
    Never raises.
    """
    # pragma: no cover - a hostile __str__
    with never_raise() as g:
        text = single_line(value, limit=limit)
    if g.failed:
        return "<unprintable>"
    raw_len = _raw_length(value)
    if raw_len is None or raw_len <= limit:
        return text
    return text.rstrip("…") + f"… (truncated, {raw_len} chars)"


def _unrecognized_items(names: Any) -> list:
    """The non-blank members of `names`; [] when it cannot be read. Never raises."""
    with never_raise(
        logger, "could not read the unrecognized name list", exc_info=True
    ):
        return [n for n in (names or []) if str(n or "").strip()]
    return []


def _unrecognized_names_line(names: Any, *, prefix: str, suffix: str) -> str:
    """The ONE rendering of ``prep_status_view()["unrecognized"]``. Never raises."""
    items = _unrecognized_items(names)
    if not items:
        return ""
    shown = [clip_echo(n, 60) for n in items[:_UNREC_SHOWN]]
    rest = len(items) - len(shown)
    return (
        prefix
        + ", ".join(f"`{s}`" for s in shown)
        + (f" (+{rest} more)" if rest > 0 else "")
        + suffix
    )
