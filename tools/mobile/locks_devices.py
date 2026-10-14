"""Device lock names and the per-device status rows.

Split out of :mod:`tools.mobile.locks` (the facade). ``device_holders`` reads
``holder`` through the ``locks_holder`` module object.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from tools.mobile import locks_holder
from tools.mobile.locks_state import _HELD, _MUTEX, DEVICE_LOCK_PREFIX


# THE ONE PLACE A SERIAL BECOMES A LOCK KEY. Every caller goes through here,
# because a serial is attacker-influenceable text that becomes a FILENAME:
# ``adb devices`` output, a ``serial=`` argument a client passed us, a
# manifest written by an older build. Two properties are required and both are
# delivered here rather than promised by the callers:
#
# * IT CANNOT ESCAPE. Everything outside ``[A-Za-z0-9._-]`` is replaced, and
#   the result is prefixed, so ``lock_path`` always gets a name matching
#   ``_NAME_RE`` and the file always lands inside ``locks/``. A hostile
#   ``../../etc/passwd`` becomes an ordinary name in the lock directory --
#   and even if this slipped, :func:`lock_path` still raises, which is the
#   second layer, not the first.
# * IT CANNOT COLLIDE. The readable part is lossy (a TCP serial's ``:`` and a
#   USB serial's ``_`` flatten to the same character, and it is truncated), so
#   readability alone would give two DIFFERENT devices one lock -- which reads
#   as "busy" for a device nobody is driving, or worse, as one hold covering
#   two phones. The digest is taken over the FULL serial and settles it.
#
# An empty or over-long serial returns ``""``: no device is named, so there is
# nothing to lock, and a caller must refuse rather than fall back to a shared
# name. A shared fallback name is the collision this function exists to make
# impossible.
def device_lock_name(serial: object) -> str:
    """The lock name for ONE device, or ``""`` when *serial* names no device."""
    text = str(serial or "").strip()
    if not text or len(text) > 128:
        return ""
    readable = re.sub(r"[^A-Za-z0-9._-]", "_", text)[:24]
    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
    return DEVICE_LOCK_PREFIX + readable + "-" + digest


def names_held_by(owner: object) -> list[str]:
    """Lock names THIS PROCESS holds under *owner*, sorted.

    The answer to "which device is this run on" that does not need a serial and
    cannot be wrong: ``_HELD`` is the fd table, and the fd IS the ownership. A
    releaser that derived the serial for itself -- from a manifest, from a
    packet -- would be a SECOND producer of that fact, and would give away the
    wrong device on the day the two disagreed (an emulator that rebooted onto a
    new serial is that day).
    """
    label = str(owner or "").strip()
    if not label:
        return []
    with _MUTEX:
        return sorted(name for name, held in _HELD.items() if held.owner == label)


def held_names() -> list[str]:
    """Lock names this process holds, sorted. Pure introspection, for tests."""
    with _MUTEX:
        return sorted(_HELD)


def _unknown_device_row(serial: str, error: str) -> dict:
    """A ``device_holders`` row for a device whose state is unknown."""
    return {
        "serial": serial,
        "held": False,
        "pid": 0,
        "owner": "",
        "age": 0.0,
        "held_too_long": False,
        "error": error,
    }


def _known_row(text: str, body: dict) -> dict:
    """A ``device_holders`` row built from a readable ``holder`` body."""
    return {
        "serial": text,
        "held": bool(body.get("held")),
        "pid": int(body.get("pid") or 0),
        "owner": str(body.get("owner") or ""),
        "age": float(body.get("age") or 0.0),
        "held_too_long": bool(body.get("held_too_long")),
        "error": "",
    }


def _row_for(serial: object, now: float | None) -> dict:
    """The ``device_holders`` row for one *serial*."""
    text = str(serial or "")
    name = device_lock_name(text)
    if not name:
        return _unknown_device_row(text, "that is not a device serial")
    probed = locks_holder.holder(name, now=now) or {}
    body = probed.get("content")
    if probed.get("error") or body is None:
        error = str(probed.get("error") or "the lock could not be read")
        return _unknown_device_row(text, error)
    return _known_row(text, body)


# The status surface, and the reason it takes a LIST. Once a lock is keyed by
# the device, "is the emulator free?" is not a question with an answer -- and
# a probe that kept asking it would read a lock nobody takes and report a busy
# phone as free. Every attached device is asked by name, and a device whose
# probe failed carries ``error`` rather than a guess, because "unknown" must
# never render as "free".
def device_holders(serials: Any, *, now: float | None = None) -> list[dict]:
    """``[{serial, held, owner, age, held_too_long, error}]``, one row per device."""
    return [_row_for(serial, now) for serial in list(serials or [])]
