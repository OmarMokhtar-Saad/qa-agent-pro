"""The qa-doctor mobile-lane section (moved from setup_check)."""

from __future__ import annotations

import logging
from typing import Optional

from config.settings_guard import never_raise
from handlers import (
    _common,
)
from handlers.mobile import setup_devices as setup_mobile_devices
from handlers.mobile import setup_emulator as setup_mobile_emulator
from handlers.mobile import setup_ime as setup_mobile_ime
from tools.mobile import ime, sdk_locator
from tools.mobile import render as doctor_render

logger = logging.getLogger("tools.mcp_handlers")


# Bounded on purpose: qa-doctor is the FIRST thing a tester runs on a new
# machine, and a device probe that hangs turns the one diagnostic tool into
# the symptom. The probe is wrapped in ``asyncio.wait_for``; a timeout is
# reported as "not checked", never as absent hardware -- telling a tester
# their emulator is missing because our probe was slow is exactly the class of
# untrue tester-facing text this file has been corrected for before.
async def _mobile_doctor_section() -> list:
    """The qa-doctor lines for the mobile lane. Never raises, never blocks."""
    if not _common.mobile_lane_enabled():
        return []
    attached = await _attached_or_none()
    physical = setup_mobile_emulator.has_physical(attached)
    lines = [
        _lane_heading(physical),
        "- Device actions go through `qa_mobile_test` (goal=... starts an exploration"
        " run), not raw adb: it owns the safety guard, keyboard handling and"
        " evidence.",
    ]
    lines += await _mobile_sdk_ime_lines(physical, attached)
    lines += await setup_mobile_emulator._mobile_emulator_lines(physical)
    return lines


def _lane_heading(physical: bool) -> str:
    """The section title; a phone on the cable is named, not called an emulator."""
    if physical:
        return "### Mobile device lane (a physical phone is attached)"
    return "### Mobile emulator lane"


async def _attached_or_none() -> Optional[dict]:
    """The doctor run's ONE devices read; None when the probe fails."""
    with never_raise(logger, "qa-doctor device probe skipped", exc_info=True):
        return await setup_mobile_devices._mobile_attached_devices()
    return None


def _sdk_line(root: str, located: dict, render) -> str:
    return (
        "- ✅ Android SDK found at `"
        + root
        + "` ("
        + str(located.get("source") or "?")
        + ")"
        if root
        else "- ⬜ Android SDK not found → `qa_mobile_test` answers with a "
        "setup guide (install Android Studio: "
        + render.ANDROID_STUDIO_URL
        + "). This server downloads no SDK."
    )


async def _mobile_sdk_ime_lines(
    physical: bool = False, attached: Optional[dict] = None
) -> list:
    """SDK, AVD, device-lock and keyboard lines; one note when they cannot be read."""
    lines: list = []
    with never_raise(
        logger, "qa-doctor mobile sdk/ime section skipped", exc_info=True
    ) as g:
        located = (sdk_locator.locate_sdk() or {}).get("content") or {}
        root = str(located.get("sdk_root") or "")
        lines.append(_sdk_line(root, located, doctor_render))
        if root:
            lines += await setup_mobile_emulator._mobile_avd_rows(physical)
        await _mobile_lock_ime_lines(lines, attached)
    if g.failed:
        lines.append("- ⬜ Mobile support modules could not be inspected")
    return lines


# THE DEVICE LOCKS, ONE PER DEVICE, ASKED BY NAME. The design's honest
# bound (plan §5.6) is that a holder whose heartbeat writer never
# started is NOT force-released -- breaking a live holder's lock is the
# two-holder defect the whole mechanism deletes -- and that the cost of
# that choice is disclosed instead. This section IS that disclosure.
#
# IT MUST NAME THE DEVICE. This was `holder()` with no argument, taking
# the module default; once a lock is keyed by the device it holds, that
# probe reads a lock nobody ever takes and reports "free" while a device
# is being driven -- the same consumer-left-behind failure this file's
# `content is None` branch already exists for, one layer up.
# AND WHETHER IT WORKS ON THE DEVICES IN FRONT OF US. The pin above is
# a fact about this BUILD; it says nothing about whether any attached
# device can type a character, which is what a tester whose script typed
# nothing is actually asking.
async def _mobile_lock_ime_lines(lines: list, attached: Optional[dict] = None) -> None:
    """Append the per-device lock rows and the QA keyboard rows to *lines*.

    *attached* is the caller's one devices read (None: read it here)."""

    if attached is None:
        attached = await setup_mobile_devices._mobile_attached_devices()
    lines += setup_mobile_devices._mobile_lock_rows(attached)
    pinned = (ime.manifest_status() or {}).get("content") or {}
    lines.append(setup_mobile_ime.pinned_row(pinned))
    if pinned.get("ok"):
        lines += await setup_mobile_ime._mobile_ime_rows(
            [str(s) for s in ((attached or {}).get("content") or [])]
        )
