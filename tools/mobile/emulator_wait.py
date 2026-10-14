"""Waiting for an emulator to list in adb and to finish booting (S16)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from tools import settings_port
from tools.mobile import adb, watch
from tools.mobile.emulator_host import host

logger = logging.getLogger("tools.mobile.emulator")

#: How often the boot poll asks the device. Half a second (S16): a boot that
#: finished, or an emulator that died, is seen within one short poll.
POLL_INTERVAL_S = 0.5

#: How often ``boot()`` asks adb whether the new emulator has a serial yet.
#: Slower than :data:`POLL_INTERVAL_S` on purpose: a cold emulator takes tens
#: of seconds to appear, each ask is an ``adb devices`` round trip, and a
#: process that died is caught by ``exit_report`` on the same tick.
BOOT_SERIAL_POLL_S = 2.0

#: A booted device answers this with ``1``.
BOOT_PROP = "sys.boot_completed"


def boot_timeout_s() -> int:
    """Operator-tunable boot budget (``QA_MOBILE_BOOT_TIMEOUT_S``)."""
    try:
        return max(30, int(settings_port.get().qa_mobile_boot_timeout_s))
    except (
        AttributeError,
        TypeError,
        ValueError,
        OverflowError,
    ):  # pragma: no cover - coercer guarantees an int
        return 240


@dataclass(frozen=True)
class BootOpts:
    """How ``wait_boot`` words its timeout and which AVD it watches."""

    tunable: bool = True
    avd: str = ""


_BOOT_OPTS = BootOpts()


def _remedy(tunable: bool) -> str:
    """The ONE thing that decides the remedy: whose budget was this?"""
    # Both branches name Android Studio, because warming the snapshot
    # shortens the BOOT and so helps either way; only the first offers the
    # setting, because only there does raising it change what happens next.
    if tunable:
        return (
            "Raise QA_MOBILE_BOOT_TIMEOUT_S, or start the AVD from "
            "Android Studio once to warm its snapshot."
        )
    return (
        "Nothing is blocked on it: QA_MOBILE_BOOT_TIMEOUT_S does not "
        "extend this slice, which is the most one tool call may spend "
        "before it answers. A cold emulator commonly needs a minute or "
        "two, so wait and ask again; start the AVD from Android Studio "
        "once to warm its snapshot if it always takes this long."
    )


def _boot_timeout_error(budget: int, last: str, tunable: bool) -> str:
    """The message for a boot that outlived its *budget*."""
    return (
        "The emulator did not finish booting within "
        + str(budget)
        + "s (last "
        + BOOT_PROP
        + "="
        + repr(last[:60])
        + "). "
        + _remedy(tunable)
    )


def _dead_check(avd: str) -> dict | None:
    """The error result when *avd*'s emulator already exited, else None."""
    if not avd:
        return None
    gone = host().exit_report(avd, when="before it finished booting")
    return {"error": gone["error"], "content": None} if gone else None


async def _probe(serial: str, ticker: watch.Ticker) -> tuple[bool, str]:
    """Ask the device once: ``(booted, last answer)``."""
    await ticker.tick()
    prop = await adb.getprop(serial, BOOT_PROP)
    last = (
        str(prop.get("content") or "")
        if not prop.get("error")
        else str(prop.get("error"))
    )
    booted = not prop.get("error") and str(prop.get("content") or "").strip() == "1"
    return booted, last


async def _wait_loop(serial: str, budget: int, opts: BootOpts) -> dict:
    """Poll ``sys.boot_completed`` until it is ``1`` or *budget* seconds pass."""
    deadline = time.monotonic() + budget
    last = ""
    ticker = watch.Ticker("emulator booting")
    while time.monotonic() < deadline:
        gone = _dead_check(opts.avd)
        if gone:
            return gone
        booted, last = await _probe(serial, ticker)
        if booted:
            return {"error": None, "content": {"serial": serial, "booted": True}}
        await asyncio.sleep(POLL_INTERVAL_S)
    return {"error": _boot_timeout_error(budget, last, opts.tunable), "content": None}


# ``tunable`` says whether ``QA_MOBILE_BOOT_TIMEOUT_S`` governs the budget this
# call actually spent, and it changes the TIMEOUT MESSAGE only -- never the
# waiting. True for every caller whose budget comes from that setting: the
# default, and ``boot``, which slices its own remaining time off it. False for a
# caller that imposed a bounded slice of its own and hands back a pointer rather
# than waiting (``session.ensure_device``, 20s). There the setting cannot move
# this wait, and a remedy naming it sends the tester to turn a knob that is not
# connected to anything they can see.
#
# WATCHED (S16): with ``avd`` named, an emulator process that exits mid-boot ends
# the wait at once in its own words (``exit_report``) instead of after the whole
# budget, and every poll may send the host a progress line.
#
# Poll until ``sys.boot_completed`` is ``1``. ``adb wait-for-device`` returns as
# soon as adbd answers, which is minutes before the launcher exists; every "the
# package is not installed" failure on a cold emulator traces back to trusting it.
async def wait_boot(serial: str, timeout: int = 0, opts: BootOpts = _BOOT_OPTS) -> dict:
    """Poll until ``sys.boot_completed`` is ``1``."""
    try:
        return await _wait_loop(serial, int(timeout or boot_timeout_s()), opts)
    except Exception as exc:
        logger.exception("mobile.emulator.wait_boot failed")
        return {"error": str(exc), "content": None}


async def _await_serial(avd: str, deadline: float) -> tuple[str, dict | None]:
    """Poll ``adb devices`` until *avd* lists a serial or *deadline* passes.

    Returns ``(serial, failure)``: *failure* is the error result when the
    emulator process died first, else ``None``; *serial* is empty on timeout.
    """
    serial = ""
    ticker = watch.Ticker("emulator starting")
    while time.monotonic() < deadline and not serial:
        await asyncio.sleep(BOOT_SERIAL_POLL_S)
        await ticker.tick()
        found = (await host().find_running(avd)).get("content") or {}
        serial = str(found.get("serial") or "")
        if not serial:
            # B5: a process that already died will never appear in adb.
            # Say so now, in the emulator's own words, not after the budget.
            gone = host().exit_report(avd)
            if gone:
                return serial, {"error": gone["error"], "content": None}
    return serial, None


def _never_listed_error(avd: str, pid: int, budget: int) -> str:
    """The message for an emulator that was spawned but never reached adb."""
    return (
        str(avd)
        + " was started (pid "
        + str(pid)
        + ") but never appeared in `adb devices` within "
        + str(budget)
        + "s. Start it once from Android Studio to see the "
        "emulator's own error."
    )
