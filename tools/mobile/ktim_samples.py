"""Timing samples: the stored shape and the ops whose duration is no readiness signal.

``samples_json`` is ``{"v": 2, "ms": [...]}``. Rows written before the fix hold a
bare list: their samples include our own pre-step sleep (the learned wait fed
itself up to the cap), so they are ignored on read and replaced on the next
write. No manual step; the row heals as soon as a run teaches it again.
"""

from __future__ import annotations

import json

VERSION = 2

#: Ops whose own duration is not the app's readiness: ``wait`` and the
#: ``wait_until_*`` polls ARE a wait, ``fill``/``type``/``clear`` cost typing
#: time, ``tap_text_if_present`` polls for its label, the rest end the run.
UNREADY_OPS = frozenset(
    {
        "wait",
        "done",
        "ask_tester",
        "assert",
        "fill",
        "type",
        "clear",
        "wait_until_changed",
        "wait_until_text",
        "wait_until_gone",
        "wait_until_idle",
        "tap_text_if_present",
    }
)


def encode(samples: list) -> dict:
    """The value to store for *samples*."""
    return {"v": VERSION, "ms": [int(v) for v in samples]}


def decode(raw: object) -> list | None:
    """The samples of a current row; None for a legacy (poisoned) or broken one."""
    try:
        got = json.loads(str(raw or "[]"))
    except (ValueError, TypeError, OverflowError):
        return None
    if got == []:
        return []
    if not isinstance(got, dict) or got.get("v") != VERSION:
        return None
    return [int(v) for v in got.get("ms") or [] if isinstance(v, (int, float))]
