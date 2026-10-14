"""Mobile session: the machine-wide install record. Cut verbatim from tools/mobile/session_device.py (w17 sessA)."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from tools.mobile import paths

logger = logging.getLogger("tools.mobile.session")

INSTALL_FILE = "install.json"


def _install_state_path() -> Path:
    return paths.state_file(INSTALL_FILE)


#: SECONDS an install record may be old and still support the claim "the
#: install is still running". The record lives at ONE machine-wide path with no
#: run identity, exactly like the provisioning record ``render`` ages, so past
#: this age it describes an install the reader never started. Sized off the
#: thing it bounds: an `adb install -r -g` of a large APK over a slow emulator
#: bridge is minutes, not hours, so an hour is generous for the real case and
#: still far short of the days that produced the defect. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
INSTALL_PENDING_MAX_AGE_S = 3600


# This was ``float(record.get("started") or 0)`` inline. ``float()`` raises
# OverflowError on a sufficiently large int and ValueError on a non-numeric
# string, and the exception unwound into ``install_state``'s outer
# ``except Exception`` -- so one corrupt field in a machine-wide file turned
# a routine "is this app installed" answer into a FAILED CALL, which the
# caller renders as a broken device rather than as a healthy no.
#
# Found by strengthening the oversized-stamp test to assert the ENVELOPE
# rather than only the verdict: the aging guard beside it was catching its
# own OverflowError correctly, and this second coercion was not.
#
# A NON-FINITE VALUE DOES NOT RAISE, so the except never sees it.
# `json.loads` accepts bare NaN, Infinity and -Infinity, and this record is
# a machine-wide file this process did not write. Every comparison against
# NaN is False, so a NaN returned here walks through the freshness checks
# downstream instead of tripping them -- the same shape that let a NaN
# timestamp make the run collector delete a live run. `or 0` is a default
# for a FALSY value and NaN is truthy, so it is not a guard either.
def _started_seconds(record: dict) -> float:
    """The record's start time as a float, or ``0.0`` for anything unusable."""
    try:
        started = float(record.get("started") or 0)
    except (OverflowError, ValueError, TypeError):
        return 0.0
    if not math.isfinite(started):
        return 0.0
    return started


def _install_is_recent(record: dict, now: float) -> bool:
    """Did the install this record describes start recently enough to still run?

    Same rule, same reason, as ``render._record_is_current``: a machine-wide
    record with no identity is only as good as its age, so a MISSING or
    non-numeric ``started`` is not recent -- an undated record cannot support a
    claim about now -- and neither is a stamp in the future.
    """
    stamp = record.get("started")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        return False
    try:
        age = float(now) - float(stamp)
    except (OverflowError, ValueError):
        return False
    return 0 <= age <= INSTALL_PENDING_MAX_AGE_S


def _read_install_record() -> dict:
    """The last started install, or ``{}``.

    Unreadable is deliberately not an error: the DEVICE is the authority on
    whether the package is present, and this record only says what was started.
    """
    try:
        target = _install_state_path()
        if not target.is_file():
            return {}
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else {}
    except (OSError, ValueError):
        logger.info("mobile.session: unreadable install state")
        return {}
