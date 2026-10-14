"""Android (``adb``) device listing for ``tools.device_manager``."""

from __future__ import annotations

import asyncio
import logging

from tools.device_ids import parse_adb_devices
from tools.device_manager_host import host
from tools.device_manager_remedy import adb_state_remediation
from tools.device_manager_spawn import _arun_start_server, _cmd_timeout

logger = logging.getLogger("tools.device_manager")


def _android_name(parsed: dict, serial: str) -> str:
    model = str((parsed.get("metadata") or {}).get("model") or "")
    return (model.replace("_", " ") or serial) if model else serial


def _android_base(parsed: dict) -> dict:
    serial = str(parsed.get("serial") or "")
    return {
        "id": serial,
        "name": _android_name(parsed, serial),
        "platform": "android",
        "kind": "emulator" if serial.startswith("emulator-") else "device",
        "state": str(parsed.get("state") or "unknown"),
    }


# One ``parse_adb_devices`` row as a device dict for the listing API.
#
# EVERY row gains a ``state`` key, usable ones included (always ``device``).
# That is ADDITIVE-SAFE, not byte-identical to the old shape: every consumer
# of this list reads named fields through ``.get()`` and none iterates the
# keys or compares whole dicts, so nothing is disturbed -- but the row is not
# the same object it was, and a comment claiming otherwise would be a lie the
# next reader trusts.
def _android_row(parsed: dict) -> dict:
    """One ``parse_adb_devices`` row as a device dict for the listing API."""
    row = _android_base(parsed)
    if not parsed.get("usable"):
        row["raw_state"] = str(parsed.get("raw_state") or "")
        row["remediation"] = adb_state_remediation(
            parsed.get("state"), parsed.get("raw_state")
        )
    return row


class _AndroidListing(tuple):
    """``(usable, unusable, dropped_lines)`` plus a ``.problem`` sentence.

    A tuple subclass so every existing consumer that unpacks three values keeps
    working; ``problem`` is None when adb answered cleanly, else one sentence
    saying what went wrong and how to fix it (B4). Built from ``parts``, the
    three-tuple (one parameter object instead of three positional lists).
    """

    problem: str | None

    def __new__(cls, parts, problem=None):
        self = super().__new__(cls, parts)
        self.problem = problem
        return self


# Bounded, quiet ``adb start-server``. None when fine, else a sentence.
#
# EVERY stdio handle is DEVNULL on purpose: the daemon it forks inherits them,
# and a daemon holding our pipe makes ``communicate()`` wait for the daemon to
# exit, which it never does. A missing adb is not a problem here (the listing
# step reports that itself). Never raises.
async def start_adb_server() -> str | None:
    """Bounded, quiet ``adb start-server``. None when fine, else a sentence."""
    r, spawned = await _arun_start_server(host()._adb_binary())
    if r.timed_out:
        logger.warning(
            "device_manager: adb start-server timed out after %ss",
            host().ADB_START_SERVER_TIMEOUT_S,
        )
        return _start_server_hint()
    if r.exc is None or (isinstance(r.exc, OSError) and not spawned):
        return None
    if not spawned:
        raise r.exc
    logger.debug("device_manager: adb start-server probe failed", exc_info=r.exc)
    return None


def _start_server_hint() -> str:
    """The fix sentence shown when ``adb start-server`` timed out."""
    timeout = host().ADB_START_SERVER_TIMEOUT_S
    return (
        f"`adb start-server` did not finish within {timeout:.0f} s. "
        "Fix: run `adb kill-server` then `adb start-server` in a terminal; if it "
        "still hangs, another adb (Android Studio, a phone-mirroring tool) may "
        "hold port 5037 -- close it and retry."
    )


def _devices_timeout_hint() -> str:
    return (
        f"`adb devices -l` did not answer within {_cmd_timeout()} s. "
        "Fix: run `adb kill-server` then `adb start-server`, then retry."
    )


def _devices_failed(err: bytes) -> _AndroidListing:
    detail = err.decode(errors="replace")[:200]
    logger.warning("device_manager: adb devices failed: %s", detail)
    return _AndroidListing(([], [], 0), f"`adb devices -l` failed: {detail}")


def _android_listing(out: bytes, problem: str | None) -> _AndroidListing:
    rows = parse_adb_devices(out.decode(errors="replace"))
    usable = [_android_row(parsed) for parsed in rows if parsed.get("usable")]
    unusable = [_android_row(parsed) for parsed in rows if not parsed.get("usable")]
    # THE THIRD ELEMENT IS THE POINT: a list that is missing transports must
    # not be reported as the whole truth. `list_devices` carries it to the
    # renderer, which tells the tester in our own prose.
    return _AndroidListing((usable, unusable, rows.dropped_lines), problem)


# ``(usable, unusable)`` from ``adb devices -l``. ``([], [])`` if adb missing.
#
# The UNUSABLE list is the point: a phone whose USB-debugging prompt has not
# been accepted is physically attached and must be reported, with what to do
# about it, rather than dropped. A serial that fails the id whitelist is
# reported here too -- it is rendered (inside an untrusted block) and never
# passed to a subprocess.
async def list_android_all() -> tuple[list[dict], list[dict], int]:
    """``(usable, unusable, dropped_lines)`` from ``adb devices -l``."""
    h = host()
    try:
        problem = await h._start_adb_server()
        cmd = [h._adb_binary(), "devices", "-l"]
        rc, out, err = await h._run(cmd, _cmd_timeout())
    except FileNotFoundError:
        logger.info("device_manager: adb not installed -- skipping Android devices")
        return _AndroidListing(([], [], 0))
    except asyncio.TimeoutError:
        # Was a silent ([], [], 0): the tester saw "no devices" for a wedged adb.
        return _AndroidListing(([], [], 0), problem or _devices_timeout_hint())
    if rc != 0:
        return _devices_failed(err)
    return _android_listing(out, problem)


# Devices/emulators ``adb`` can actually drive. Empty list if adb missing.
#
# A thin filter over ``list_android_all`` -- kept so callers that only ever
# wanted drivable devices keep their exact contract.
#
# The dropped-line count is DELIBERATELY discarded here: this function's
# whole contract is "drivable devices, exactly the old shape", it has no
# production caller, and the reporting path is `list_devices`, which does
# carry it. Widening this signature would move the disclosure away from the
# one place a person reads.
async def _list_android() -> list[dict]:
    """Devices/emulators ``adb`` can actually drive. Empty list if adb missing."""
    usable, _unusable, _dropped = await host().list_android_all()
    return usable
