"""Mobile session: the finish_device reply envelope. Cut from tools/mobile/session_finish.py (w17 sessA)."""

from __future__ import annotations


def _slot(reply: dict) -> dict:
    """A restore reply's content, or the not-restored shape carrying its error."""
    return reply.get("content") or {
        "restored": False,
        "detail": str(reply.get("error") or ""),
    }


# Carried rather than swallowed: a restore that failed is a device
# left on the QA keyboard, and the reply a tester reads is the only
# place that can say so.
#
# A SECOND VALUE WITH A SECOND NAME, not a caveat on the first.
# "the keyboard this run displaced" and "a keyboard an earlier,
# crashed run displaced" are different facts with different owners,
# and a reader that had to tell them apart from one field would be
# guessing.
def _finish_device_reply(released: dict, restores: tuple, stale: tuple) -> dict:
    """The ``finish_device`` envelope from the release result and both restores."""
    restored, anim, a11y_restored = restores
    swept, anim_stale, a11y_stale = stale
    return {
        "error": None,
        "content": {
            "released": bool((released.get("content") or {}).get("released")),
            "release": released.get("content") or {},
            "ime": _slot(restored),
            "ime_stale": swept,
            "animations": _slot(anim),
            "animations_stale": anim_stale,
            "a11y": _slot(a11y_restored),
            "a11y_stale": a11y_stale,
        },
    }
