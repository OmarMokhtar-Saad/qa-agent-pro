"""``relabel``: rename the in-memory owner of a lock this process holds.

Split out of :mod:`tools.mobile.locks` (the facade).
"""

from __future__ import annotations

import logging

from tools.mobile import locks_body
from tools.mobile.locks_state import _HELD, _MUTEX, EMULATOR_LOCK, _refuse_name

logger = logging.getLogger("tools.mobile.locks")


def _relabel_refusal(reason: str, holder_label: str) -> dict:
    """The ``relabel`` refusal envelope."""
    return {
        "error": None,
        "content": {"relabelled": False, "reason": reason, "holder": holder_label},
    }


def _relabel_held(key: str, old: str, new: str) -> dict:
    """Swap the owner label of the lock *key* this process holds."""
    with _MUTEX:
        mine = _HELD.get(key)
        if mine is None:
            return _relabel_refusal("not held", "")
        if mine.owner != old:
            # `holder` is the real current OWNER LABEL, not prose. A caller
            # rendering this refusal needs a label it can put in a
            # `run_id=` instruction; handing it the reason string produced
            # `run_id="held by mrun-..."`, which cannot work.
            return _relabel_refusal("held by " + mine.owner, mine.owner)
        mine.owner = new
        locks_body._stamp(key, mine.fd, locks_body.Stamp(new, mine.since))
        return {"error": None, "content": {"relabelled": True, "reason": ""}}


def _relabel_checked(name: str, from_owner: str, to_owner: str) -> dict:
    """``relabel`` without the never-raise wrapper."""
    refusal = _refuse_name(name)
    if refusal is not None:
        return refusal
    new = str(to_owner or "").strip()
    if not new:
        return {
            "error": "A lock needs an owner; refusing to relabel to nothing.",
            "content": None,
        }
    return _relabel_held(str(name), str(from_owner or "").strip(), new)


# The SAME fd keeps the SAME kernel lock: this is not release-then-reacquire,
# which would open a window in which another process could take the device
# between the two. It exists for one real sequence -- the new-run path must
# hold the device BEFORE a ``run_id`` exists, so it acquires under
# a label from :func:`new_provisioning_owner` and relabels once the run is
# planned.
def relabel(name: str = EMULATOR_LOCK, *, from_owner: str, to_owner: str) -> dict:
    """Rename the in-memory owner of a lock this process holds. ``{relabelled}``."""
    try:
        return _relabel_checked(name, from_owner, to_owner)
    except Exception as exc:
        logger.exception("mobile.locks.relabel failed")
        return {"error": str(exc), "content": None}
