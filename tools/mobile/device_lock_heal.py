"""Give back a same-process pre-run hold whose setup call has ENDED (fix/1520).

A pre-run (provisioning) owner is a label one ``qa_mobile_test`` call mints; it
has no run and no lease heartbeat, so the ONLY thing that can release it is
that call's own ``finally``. When the host cancels the call mid-await, anyio's
level-triggered cancellation can kill that ``finally`` before its sync release
runs, and the hold then outlives the call for as long as the server lives.

This module is the repair, and it is deliberately narrow: a hold is an orphan
ONLY when all four are true --

* it is held IN THIS PROCESS (``same_process``; another process's hold is
  never touched, its owner may be mid-install);
* the holder is a provisioning label (a run id has a lease heartbeat and a
  real owner, and is never healed here);
* the holder is not the caller itself;
* :mod:`tools.mobile.prerun_inflight` recorded that owner's setup call as
  ENDED with the device still held. A running call is a LIVE holder, and a
  label that module never saw (``tools.mobile_capture`` mints the same kind
  for the cert setup, capture setup and teardown, and holds across long
  awaits) is a live holder too: force-releasing under either would be two
  chats on one device.

Logger name kept as ``tools.mobile.session`` for the log filters.
"""

from __future__ import annotations

import logging

from tools.mobile import locks, prerun_inflight

logger = logging.getLogger("tools.mobile.session")


def orphan_holder(took: object, label: str) -> str:
    """The orphaned same-process pre-run holder blocking *label*, or ``""``."""
    body = (took if isinstance(took, dict) else {}).get("content") or {}
    if body.get("acquired") or not body.get("same_process"):
        return ""
    holder = str(body.get("holder") or "")
    if not holder or holder == label or not locks.is_provisioning_owner(holder):
        return ""
    if not prerun_inflight.has_ended(holder):
        return ""
    return holder


def release_orphan(name: str, holder: str) -> bool:
    """Force-release *name* from the orphan *holder*. ``True`` when it let go."""
    logger.warning(
        "mobile.session: pre-run hold on %s by %s outlived its setup call; "
        "giving it back",
        name,
        holder,
    )
    done = locks.release(name, locks.Release(owner=holder, force=True))
    released = bool(((done or {}).get("content") or {}).get("released"))
    if released:
        prerun_inflight.forget(holder)
    return released
