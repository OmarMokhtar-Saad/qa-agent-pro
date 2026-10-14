"""The live watches of this process, the freshness bound and ``status``.

``_WATCHES`` is defined here ONCE: ``network_watch`` re-exports the same dict,
and the start and stop leaves mutate it, so nothing ever rebinds it.
"""

from __future__ import annotations

import time

from tools.mobile_evidence.network_watch_key import _key

#: Watches live in this process only. A watch is a live console capture plus a
#: sampler thread, and both die with the server -- persisting a handle would
#: promise a caller it could resume something that is already gone.
_WATCHES: dict = {}

#: How long one watch may run before a `status` call reports it as EXPIRED and
#: refuses to keep claiming it is live. A watch holds an emulator console
#: capture and a once-a-second sampler; a GUI that crashes with one open would
#: otherwise leave both running with nothing to stop them. The number is a
#: freshness bound on a CLAIM, not a cap on work.
MAX_WATCH_S = 3600

ACTIONS = ("start", "stop", "status")


def _now() -> float:
    return time.time()


def _entry(key: str, live: dict, stamp: float) -> dict:
    age = max(0.0, stamp - float(live["started_at"]))
    return {
        "serial": key,
        "state": "expired" if age > MAX_WATCH_S else "watching",
        "age_s": round(age, 1),
        # An expired watch is reported, never silently reaped: the
        # console capture behind it may still be running, and a reader
        # told "nothing is watching" would never stop it.
        "note": (
            "Older than the watch freshness bound -- stop it; the console "
            "capture behind it may still be recording."
        )
        if age > MAX_WATCH_S
        else "",
    }


def status(serial: str = "", now: float | None = None) -> dict:
    """What is being watched, and for HOW LONG. Reads memory, touches nothing."""
    stamp = _now() if now is None else float(now)
    out = [
        _entry(key, live, stamp)
        for key, live in _WATCHES.items()
        if not serial or _key(serial) == key
    ]
    return {"error": None, "content": {"watches": out}}
