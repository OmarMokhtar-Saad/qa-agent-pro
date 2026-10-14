"""The emulator-booted step of the preflight, and the choice of one device."""

from __future__ import annotations

import logging

from tools.mobile import adb, emulator
from tools.mobile import preflight_rows as rows
from tools.mobile.preflight_consts import _STEP_ERRORS
from tools.mobile.preflight_rows import _failed

logger = logging.getLogger("tools.mobile.preflight")


def _ambiguous_row(serials: list[str]) -> dict:
    """The ``device_choice`` row: several devices, none safe to choose."""
    ambiguous = ", ".join(str(s) for s in serials[:6])
    return rows.record_row(
        "device_choice",
        rows._Row(
            False,
            "more than one device is attached and none could be "
            "chosen safely: " + ambiguous,
            "Name the device explicitly, because this lane "
            "installs apps and taps the screen and must never "
            "guess which one. Pass the emulator's serial, or "
            "detach the others.",
        ),
    )


# The one device this lane may pick for itself, or "" (a row says why).
#
# NEVER serials[0]. adb's order is not stable, and this lane
# installs, types, taps and force-stops -- so picking the
# first attached device can drive a tester's own phone.
#
# The rule has TWO halves and both are pinned, because each one
# alone reads like a bug to whoever meets it next:
#
#  * AMBIGUOUS set -> an emulator is the only device this lane
#    may choose for itself, and two of them refuse by name
#    rather than guess (`test_a_phone_is_never_chosen_over_an_
#    emulator`, `test_two_emulators_refuse_by_name_rather_than_
#    guess`).
#  * EXACTLY ONE attached device -> it is chosen, emulator or
#    not. Nothing is ambiguous about one device, and a tester
#    who runs the lane against a single physical handset on
#    purpose is a supported case; refusing it would be a guard
#    that stops the supported flow
#    (`test_a_single_attached_device_is_still_allowed`).
#
# Reaching either branch at all requires a caller that resolved
# no serial of its own; every production caller resolves one
# first, so this is the fallback, not the usual path.
def _choose_serial(checks: list[dict], serials: list[str]) -> str:
    """The one device this lane may pick for itself, or ``""`` (a row says why)."""
    emulators = [s for s in serials if str(s).startswith("emulator-")]
    if len(emulators) == 1:
        return emulators[0]
    if len(serials) == 1:
        return serials[0]
    checks.append(_ambiguous_row(serials))
    return ""


def _no_emulator_row() -> dict:
    """The ``emulator_booted`` row when no device was found."""
    return rows.record_row(
        "emulator_booted",
        rows._Row(
            False,
            "no running emulator or device was found",
            "Start the emulator from the mobile lane (or from "
            "Android Studio) and try again.",
        ),
    )


async def _boot_row(resolved_serial: str) -> dict:
    """The ``emulator_booted`` row read from the device's boot property."""
    prop = await adb.getprop(resolved_serial, emulator.BOOT_PROP)
    booted = not prop.get("error") and str(prop.get("content") or "").strip() == "1"
    detail = (
        resolved_serial
        + ": "
        + emulator.BOOT_PROP
        + "="
        + str(prop.get("content") or prop.get("error") or "?")
    )
    fix = (
        ""
        if booted
        else "The device is visible but still booting. Wait "
        "for the launcher to appear, then retry."
    )
    return rows.record_row("emulator_booted", rows._Row(booted, detail, fix))


async def _step_emulator_booted(
    checks: list[dict], serials: list[str], resolved_serial: str
) -> str:
    """4. emulator booted. Returns the serial in force (it may be chosen here)."""
    try:
        if not resolved_serial and serials:
            resolved_serial = _choose_serial(checks, serials)
        if not resolved_serial:
            checks.append(_no_emulator_row())
        else:
            checks.append(await _boot_row(resolved_serial))
    except _STEP_ERRORS as exc:
        checks.append(_failed("emulator_booted", exc))
    return resolved_serial
