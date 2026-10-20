"""Fields the packet repeats unchanged every step: sent whole once, then a pointer."""

from __future__ import annotations

import hashlib
import json

from tools.mobile import run_seen

#: Three forms each (case / escape / explore), so they are keyed by digest, not
#: dropped: a form change inside a run sends the new text whole.
REPEATED_FIELDS = ("instruction", "worker_instructions")

SAME_AS_BEFORE = "unchanged since your last packet of this run"


def _digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def repeat_once(shown: dict, run_id: str) -> dict:
    """*shown* with each repeated field replaced by a pointer once it was sent."""
    out = dict(shown)
    for name in REPEATED_FIELDS:
        value = out.get(name)
        if isinstance(value, str) and run_seen.note_seen(run_id, name, _digest(value)):
            out[name] = SAME_AS_BEFORE
    return out
