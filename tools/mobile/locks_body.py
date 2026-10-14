"""Lock bodies: the path a name maps to, the diagnostic stamp and the age reading.

Split out of :mod:`tools.mobile.locks` (the facade).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, NamedTuple

from tools.mobile import paths
from tools.mobile.locks_state import _NAME_RE

logger = logging.getLogger("tools.mobile.locks")


#: The earliest wall-clock a lock body could plausibly have been stamped at
#: (2020-09-13). A RANGE, deliberately, and not a fourth list of bad shapes.
#:
#: Three review rounds closed this by enumerating: first `""`, then unparseable
#: bodies, then `true` / huge ints / `inf` / `NaN` / future stamps. Each list was
#: complete for the values somebody had thought of, and each time a value nobody
#: had thought of walked through -- most recently `{"time": 1e-300}`, which is a
#: positive float, passes `> 0`, and produces an age of 1.8e9 seconds: a
#: CRITICAL log about a wedged holder and a qa-doctor line telling a tester to
#: quit the editor. A timestamp is only a timestamp inside a plausible window,
#: and everything outside it means "the body does not say", which is what
#: :func:`_body_age` already promises.
_PLAUSIBLE_FLOOR_S = 1_600_000_000.0


# THE GATE LIVES HERE, at the single place a name becomes a path, rather than
# in ``acquire`` alone. ``holder`` used to skip it, and ``holder`` calls
# ``_probe_free``, which CREATES the file: an executing review turned that into
# 0600 files at ``locks/../escape.lock`` and at an absolute path outside the
# cache entirely, and scattered nineteen junk files through a real
# ``~/.qa-agents/mobile/locks``. One check at the choke point cannot be
# skipped by a new call site.
#
# A ``ValueError`` rather than a dict, because every caller is already inside
# a ``try`` that turns it into ``{"error", "content"}`` -- the never-raise
# contract is kept at the public boundary, which is where it is promised.
def lock_path(name: str) -> Path:
    """The file a lock name maps to. Raises on a name that is not one."""
    if not _NAME_RE.match(str(name or "")):
        raise ValueError("Refusing " + repr(str(name)[:40]) + " as a lock name.")
    return paths.sub("locks") / (str(name) + ".lock")


def read(name: str) -> dict | None:
    """The lock body, for MESSAGES AND LOGS ONLY. Never a decision input."""
    try:
        target = lock_path(name)
        if not target.is_file():
            return None
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else None
    except (OSError, ValueError):
        return None


def _as_float(raw: Any) -> float | None:
    """*raw* as a float, or ``None`` when it is not a number."""
    # `True` is an int in Python, and `float(True) == 1.0` sails past a `> 0`
    # gate -- so a body of `{"time": true}` produced an age of 1.8e9 and a
    # CRITICAL about a wedged holder, which is the exact false alarm this
    # helper was extracted to stop. A bool is not a timestamp.
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return float(raw)
    except (AttributeError, TypeError, ValueError, OverflowError):
        # OverflowError is NOT a subclass of the other three: a legal JSON body
        # with a huge integer `time` raised straight out of here, and the lane
        # then refused every acquire without saying the lock file was corrupt.
        return None


def _raw_time(body: Any) -> float | None:
    """The finite ``time`` the body states, or ``None`` when it states none."""
    try:
        raw = (body or {}).get("time")
    except (AttributeError, TypeError):
        return None
    stamped = _as_float(raw)
    if stamped is None or stamped != stamped:
        return None  # NaN is not a timestamp either
    return None if stamped in (float("inf"), float("-inf")) else stamped


# ONE place, because there were two and only one of them was fixed. A body
# with no usable ``time`` used to make this arithmetic return ~1.8e9, so
# ``held_too_long`` fired on a lock taken a moment ago -- and the second site,
# inside :func:`acquire`, logged CRITICAL about a wedged holder called
# ``pid None``. A disclosure that cries wolf is worse than none, because the
# design's honest bound is that a hung holder is DISCLOSED rather than broken.
def _body_age(body: Any, moment: float) -> float:
    """How long ago the body says the lock was stamped. 0.0 when it does not say."""
    stamped = _raw_time(body)
    if stamped is None:
        return 0.0
    if not _PLAUSIBLE_FLOOR_S < stamped <= moment:
        # A future stamp is as meaningless as a missing one, and reporting a
        # negative age would put `held_too_long` on the wrong side of nothing.
        return 0.0
    return moment - stamped


class Stamp(NamedTuple):
    """What a lock body records: the owner label and the moment it was stamped."""

    owner: str
    moment: float


# Written through the locked fd rather than a temp file plus ``os.replace``:
# the old module's ``_write`` raced itself on the ``.tmp`` path and raised
# ``FileNotFoundError`` out of ``os.replace``, which the caller then saw as an
# error instead of as contention. There is nothing to race here -- we hold the
# lock -- and a body that fails to write costs a log line, not a run.
def _stamp(name: str, fd: int, stamp: Stamp) -> None:
    """Write the diagnostic body, AFTER the lock is held. Best effort."""
    try:
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "owner": str(stamp.owner)[:120],
                "time": float(stamp.moment),
            },
            sort_keys=True,
        )
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload.encode("utf-8"))
        os.truncate(fd, len(payload))
    except OSError:
        logger.info("mobile.locks: could not stamp the body of %s", name)


def _read(name: str) -> dict | None:
    """Private name kept for out-of-wave callers; delegates to :func:`read` at call time."""
    return read(name)
