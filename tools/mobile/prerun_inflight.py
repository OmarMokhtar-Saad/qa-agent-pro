"""Which pre-run owners have a setup call RUNNING or ENDED in this process.

A pre-run (provisioning) owner holds the device under a label with no run and
no lease heartbeat, for exactly one ``qa_mobile_test`` call. When the host gives
up on that call (Cursor's tool timeout, about 205 s on 2026-10-20) the MCP
library cancels the handler's cancel scope, and under anyio that cancellation
is LEVEL-TRIGGERED: every later ``await`` in the handler raises again. So the
``finally`` that gave the device back died at its first await, and the lock
stayed held for the life of the server (pid 34583 held R5CR207KY7H for ever,
and every retry was told to "call again in a moment").

Two things read this module:

* ``handlers.mobile.run_new`` enters the owner when its setup call starts and
  leaves it when the call ends, HOWEVER it ends (the leave is in a ``finally``
  with no await before it);
* ``tools.mobile.device_lock_heal`` treats a same-process provisioning holder
  whose setup call this module recorded as ENDED -- entered, then left, with
  the device still held -- as an orphan that nothing can ever release, and
  gives it back before taking the device.

ENDED, not "not running": other tool calls (``tools.mobile_capture``: the cert
setup, the capture setup, the teardown) mint the same kind of label and hold
the device across long awaits without registering here. A label this module
never saw is NEVER an orphan, so a concurrent ``qa_mobile_test`` is refused as
busy under them, as before. The ended set holds only owners that still had a
lock when they left (a clean end holds nothing), so it stays as small as the
orphans themselves and empties as they are healed.

Per process on purpose: ownership lives in this process's fd table
(``locks._HELD``) and only this process can tell whether one of its own
coroutines is still running. Another process's hold is never touched.
"""

from __future__ import annotations

import threading

from tools.mobile import locks_devices

_INFLIGHT: set[str] = set()
_ENDED: set[str] = set()
_MUTEX = threading.Lock()


def enter(owner: object) -> None:
    """Record that *owner*'s setup call is running."""
    label = str(owner or "")
    if label:
        with _MUTEX:
            _INFLIGHT.add(label)
            _ENDED.discard(label)


def leave(owner: object) -> None:
    """Record that *owner*'s setup call has ended (a no-op for an unknown owner).

    An owner that still holds a lock at this point is remembered as ENDED, the
    one state the heal acts on; an owner that holds nothing is simply forgotten.
    """
    label = str(owner or "")
    with _MUTEX:
        if label not in _INFLIGHT:
            return
        _INFLIGHT.discard(label)
        if locks_devices.names_held_by(label):
            _ENDED.add(label)


def forget(owner: object) -> None:
    """Drop *owner* from the ended set once its hold has been given back."""
    with _MUTEX:
        _ENDED.discard(str(owner or ""))


def is_inflight(owner: object) -> bool:
    """Whether *owner*'s setup call is still running in this process."""
    with _MUTEX:
        return str(owner or "") in _INFLIGHT


def has_ended(owner: object) -> bool:
    """Whether *owner*'s setup call ran here and ended with the device still held."""
    with _MUTEX:
        return str(owner or "") in _ENDED


def inflight() -> frozenset[str]:
    """Every owner with a running setup call. Introspection, for status and tests."""
    with _MUTEX:
        return frozenset(_INFLIGHT)


def ended() -> frozenset[str]:
    """Every owner whose setup call ended with the device still held."""
    with _MUTEX:
        return frozenset(_ENDED)
