"""Change-waits that saw no change: not worth saving, not worth replaying.

A ``wait_until_changed`` (or a plain ``wait``, which runs as one) whose recorded
screen key before equals the key after observed nothing change, so replaying it
only burns its whole timeout. The learner drops such a step; the replay skips
the ones older flows already hold.
"""

from __future__ import annotations

CHANGE_WAITS = frozenset({"wait_until_changed", "wait"})


def no_change(op: object, before: object, after: object) -> bool:
    """True for a change-wait whose recorded before key equals its after key."""
    return str(op or "") in CHANGE_WAITS and bool(before) and before == after


def skip_at(template: dict, pos: int) -> bool:
    """True when step *pos* of a saved flow is a change-wait that saw no change."""
    meta = template.get("meta") or {}
    steps = template.get("steps") or []
    before, after = meta.get("before") or [], meta.get("after") or []
    if not (0 <= pos < min(len(steps), len(before), len(after))):
        return False
    return no_change(steps[pos].get("op"), before[pos], after[pos])
