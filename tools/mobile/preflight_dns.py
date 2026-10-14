"""The device DNS check (step 4b): the name probe and the row built from it."""

from __future__ import annotations

import logging

from tools.mobile import adb
from tools.mobile import preflight_rows as rows
from tools.mobile.preflight_consts import _STEP_ERRORS
from tools.mobile.preflight_dns_consts import (
    DNS_PING_WAIT_S,
    DNS_PROBE_HOST,
    DNS_PROBE_TIMEOUT_S,
)
from tools.mobile.preflight_dns_fail import _dns_failure_record
from tools.mobile.preflight_rows import _Dev, _exit_code, _failed, _unanswered

logger = logging.getLogger("tools.mobile.preflight")


def dns_probe(serial: str):
    """The name-resolution ping of step 4b, as one awaitable.

    The ADB CALL carries the budget and `-W` is left to do the one job it can
    do -- see DNS_PROBE_TIMEOUT_S for the numbers that decide which is which.
    """
    return adb.shell(
        serial,
        ["ping", "-c", "1", "-W", str(DNS_PING_WAIT_S), DNS_PROBE_HOST],
        timeout=DNS_PROBE_TIMEOUT_S,
    )


# The ADB CALL carries the budget and `-W` is left to do the one job it can
# do -- see DNS_PROBE_TIMEOUT_S for the numbers that decide which is which.
#
# A probe that OUTLASTED the budget is a FINDING, not a check
# that could not be made: adb was reachable enough to be left
# waiting on the resolver. So it does NOT go to `_unanswered`,
# whose "the device did not answer" is true and tells a tester
# nothing they can act on. It falls through to the same route
# probe as an outright failure, and only the DETAIL differs.
async def _device_dns_row(resolved_serial: str, early: dict) -> dict:
    """The single ``device_dns`` row for a resolved device."""
    named = await (early.pop("dns", None) or dns_probe(resolved_serial))
    overran = bool(named.get("timed_out"))
    unanswered = None if overran else _unanswered("device_dns", named)
    if unanswered is not None:
        return unanswered
    body = named.get("content") or {}
    if not named.get("error") and _exit_code(body) == 0:
        detail = resolved_serial + " resolves " + DNS_PROBE_HOST
        return rows.record_row("device_dns", rows._Row(True, detail))
    return await _dns_failure_record(resolved_serial, overran)


async def _step_device_dns(checks: list[dict], dev: _Dev) -> None:
    """4b. the device can resolve a name."""
    try:
        if not dev.serial:
            checks.append(
                rows.record_row(
                    "device_dns",
                    rows._Row(
                        False,
                        "no device answered, so its resolver could not be asked",
                        "Start the emulator (or attach a device) and try again; "
                        "the checks above say which step is missing.",
                    ),
                )
            )
            return
        checks.append(await _device_dns_row(dev.serial, dev.early))
    except _STEP_ERRORS as exc:
        checks.append(_failed("device_dns", exc))
