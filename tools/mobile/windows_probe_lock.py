"""The lock round trip of the Windows host probe: take and release the real emulator lock."""

from __future__ import annotations

import importlib.util
import logging

from config.settings_guard import never_raise
from tools.mobile import locks

logger = logging.getLogger("tools.mobile.windows_probe")

#: The owner label the probe takes the lock under. Distinctive on purpose: if a
#: paste-back ever shows this holding a lock, the probe crashed mid-round-trip
#: and the process that would release it is gone -- which the kernel handles.
PROBE_OWNER = "windows-probe"


def _primitive() -> str:
    primitive = ""
    for name in ("fcntl", "msvcrt"):
        try:
            if importlib.util.find_spec(name) is not None:
                primitive = name
                break
        except (ImportError, ValueError):  # pragma: no cover - defensive
            continue
    return primitive or "none"


def _acquire_release(body: dict) -> None:
    taken = locks.acquire(locks.EMULATOR_LOCK, locks.Claim(owner=PROBE_OWNER))
    if taken.get("error"):
        body["detail"] = str(taken["error"])[:300]
        return
    got = taken.get("content") or {}
    body["acquired"] = bool(got.get("acquired"))
    if not body["acquired"]:
        body["detail"] = (
            "the lane's lock is held by "
            + str(got.get("owner") or "another process")
            + " -- close other chats and run this again"
        )
        return
    given = locks.release(
        locks.EMULATOR_LOCK, locks.Release(owner=PROBE_OWNER, as_holder=True)
    )
    body["released"] = not given.get("error")
    body["detail"] = str(given.get("error") or "")[:300]


# Reports the PRIMITIVE used, determined the same way ``locks._lock_fd``
# determines it -- by which module is importable -- rather than by asking
# ``locks``, so this needs no change to the locking module whose invariant is
# the most load-bearing thing in the lane.
def _lock_round_trip() -> dict:
    """Take and release the real emulator lock. Reports the PRIMITIVE used."""
    body: dict = {
        "primitive": _primitive(),
        "acquired": False,
        "released": False,
        "detail": "",
    }
    with never_raise(
        logger,
        "mobile.windows_probe: lock round trip failed: %s",
        level=logging.WARNING,
        with_exc=True,
    ) as g:
        _acquire_release(body)
    if g.failed:
        body["detail"] = str(g.exc)[:300]
    return body
