"""Mobile session: install_state, answered by the device. Cut from tools/mobile/session_device.py (w17 sessA)."""

from __future__ import annotations

import difflib
import logging
import time
from typing import NamedTuple

from tools.mobile import adb
from tools.mobile.session_device_record import (
    _install_is_recent,
    _read_install_record,
    _started_seconds,
)

logger = logging.getLogger("tools.mobile.session")

# Public seam: callers look it up here at call time, so a patch of it bites.
read_install_record = _read_install_record


class InstallProbe(NamedTuple):
    """One probe of the device plus the install record it is read against."""

    record: dict
    probed: bool
    names: list
    serial: str
    package: str


def _pending(probe: InstallProbe, installed: bool) -> bool:
    """True only when the record, the package and the device all line up."""
    return bool(
        probe.probed
        and not installed
        and probe.record.get("package") == str(probe.package)
        and str(probe.record.get("serial") or "") == str(probe.serial)
        and _install_is_recent(probe.record, time.time())
    )


# FUZZY MATCH, FROM THE SAME PROBE. `installed_packages` already listed
# every package id on the device to answer `installed` -- this
# reuses that ONE round trip rather than spending a second adb call to
# answer "did the caller guess the wrong id". A tester (or the model
# on their behalf) names an app, not a bundle id, and a wrong guess
# used to dead-end in "not installed" with no way forward short of a
# manual `adb shell pm list packages`. The cap of 5 is a LOCAL bound
# (not a module constant): a hint in an error message, not a listing,
# so it does not need the CEILINGS governance a shared cap would.
def _suggest(probe: InstallProbe, installed: bool) -> list[str]:
    """Close package ids from the probe's own listing, when nothing matched."""
    if not (probe.probed and not installed and probe.names):
        return []

    return difflib.get_close_matches(str(probe.package), probe.names, n=5, cutoff=0.4)


def _install_state_content(probe: InstallProbe) -> dict:
    """The ``install_state`` answer, derived from one probe and the record."""
    installed = bool(probe.probed and str(probe.package) in probe.names)
    return {
        "probed": bool(probe.probed),
        "installed": installed,
        "pending": _pending(probe, installed),
        "apk": str(probe.record.get("apk") or ""),
        "started": _started_seconds(probe.record),
        "suggestions": _suggest(probe, installed),
    }


# ``probed`` IS THE POINT. Whether the device answered is a fact only this
# function can know, so it SAYS so rather than leaving callers to infer it
# from the envelope. They cannot: this function's own ``error`` is set only
# in the defensive branch below, so on the failure it exists to catch it is
# ``None`` and ``probed = not state.get("error")`` reads True. A caller then
#
# ``pending`` ASSERTS EXACTLY WHAT THE RECORD ESTABLISHED, no wider. The
# record is one machine-wide file naming a package, a serial and a start
# time, so "the install is still running" needs all three to line up: the
# same package, the SAME DEVICE, and a start recent enough that the install
# could still be in flight. It used to need only the package name, so a
# five-day-old record from a different emulator still told the tester an
# install was in progress -- the same stale-machine-wide-record defect this
# lane fixed for the provisioning record, in the commit that fixed it.
# A record from another device is wrong at any age, which is why the serial
# is matched as well as the age.
# printed "`pkg` is not installed on the emulator" off a probe that never
# answered (2026-09-08, round 2).
#
# ONE PIECE OF EVIDENCE, and it is exact: ``adb.installed_packages`` reports
# an ``error`` when adb could not be run at all AND when the command ran and
# failed (a non-zero exit -- surfaced there, in the one producer of this
# list, rather than guessed at here from an empty result). So the whole rule
# is the envelope of that call, and there is no second, weaker derivation of
# it in this module.
#
# ``installed`` and ``pending`` are POSITIVE claims, made only from an
# answered probe: an unprobed call returns both False, so no caller can turn
# "we do not know" into "the install is still running".
async def install_state(serial: str, package: str) -> dict:
    """``{probed, installed, pending, apk, started}`` -- answered by the DEVICE."""
    try:
        record = read_install_record()
        listed = await adb.installed_packages(serial)
        names = list(listed.get("content") or [])
        probe = InstallProbe(
            record=record,
            probed=not listed.get("error"),
            names=names,
            serial=serial,
            package=package,
        )
        return {"error": None, "content": _install_state_content(probe)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.install_state failed")
        return {"error": str(exc), "content": None}
