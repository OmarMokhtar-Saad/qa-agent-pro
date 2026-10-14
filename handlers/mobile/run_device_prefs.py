"""Part of the mobile_run split (see handlers/mobile/run.py): device prefs."""

from __future__ import annotations

import logging

import tools.mobile.device_lock as device_lock
from config.settings_guard import never_raise
from tools.mobile import a11y, animations, ime_session
from tools.mobile import ime as mobile_ime

logger = logging.getLogger("tools.mcp_handlers")


# Notes for ``_mobile_keyboard_stage`` (moved out of the body to keep it short):
#
#     Returns markdown for the run's FIRST reply, or ``""`` when there is nothing
#     a tester needs to know -- which is the ordinary case: a keyboard that was
#     already ours, or a sweep that found nothing, is silence.
#
#     Never raises and never stops a run. Both halves are best-effort by design;
#     what they must not be is silent about a change they made to the tester's
#     device.
#
async def _mobile_keyboard_stage(run_id: str) -> str:
    """Sweep a crashed run's keyboard, make ours ready, and say what happened."""

    lines: list = []
    serial = ""
    with never_raise(
        logger, "mobile keyboard stage: no serial for the run", exc_info=True
    ):
        serial = str(device_lock.serial_of(run_id) or "")
    if not serial:
        return ""
    await _keyboard_sweep_note(serial, run_id, lines)
    await _keyboard_ready_note(serial, run_id, lines)
    return "\n\n".join(lines)


# Notes for ``_keyboard_sweep_note`` (moved out of the body to keep it short):
# THE CRASHED RUN'S KEYBOARD FIRST. Doing it after our own ensure would
# find a device already on the QA keyboard and record THAT as the thing
# to go back to -- the tester would keep it forever, which is the defect
# being fixed, reproduced by the fix.
#
# A SWEEP THAT TRIED AND FAILED IS REPORTED TOO. It used to be silent,
# which left the one person who could fix it in Settings unaware there
# was anything to fix. A sweep that found nothing to do stays silent --
# that is the ordinary case and a note on every run start is noise.
async def _keyboard_sweep_note(serial: str, run_id: str, lines: list) -> None:
    """Restore a crashed run's keyboard; append a note to *lines* if it matters."""

    with never_raise(
        logger, "mobile keyboard stage: stale sweep skipped", exc_info=True
    ):
        swept = (await ime_session.restore_stale(serial, skip_run_id=run_id) or {}).get(
            "content"
        ) or {}
        if swept.get("restored"):
            lines.append("\u2139\ufe0f " + str(swept.get("detail") or "")[:300])
        elif swept.get("previous"):
            lines.append("\u26a0\ufe0f " + str(swept.get("detail") or "")[:300])


async def _keyboard_ready_note(serial: str, run_id: str, lines: list) -> None:
    """Make our keyboard ready; append the fallback notice to *lines* on error."""

    with never_raise(logger, "mobile keyboard stage: ensure skipped", exc_info=True):
        ready = await ime_session.ensure_ready(serial, run_id)
        if ready.get("error"):
            # R1d: the run CONTINUES. Typing falls back to `adb shell input text`
            # and this says so, why, and the exact fix (the run is not blocked).

            lines.append(
                "\u26a0\ufe0f "
                + mobile_ime.fallback_notice(str(ready["error"])[:300], serial)
            )


# Notes for ``_mobile_a11y_stage`` (moved out of the body to keep it short):
#
#     ``""`` on success AND on a legacy APK (nothing to say). A failure is one line: the
#     run carries on with uiautomator. Never raises.
#
async def _mobile_a11y_stage(run_id: str) -> str:
    """Give back a crashed run's accessibility settings, enable the fast dump, say what happened."""
    serial = _run_serial(run_id, "mobile a11y stage: no serial for the run")
    if not serial:
        return ""
    lines: list = []
    await _a11y_sweep_note(serial, run_id, lines)
    await _a11y_enable_note(serial, run_id, lines)
    return "\n\n".join(lines)


def _run_serial(run_id: str, failure_log: str) -> str:
    """The serial the run holds, or ``""`` (a lookup failure is logged, not raised)."""

    with never_raise(logger, failure_log, exc_info=True):
        return str(device_lock.serial_of(run_id) or "")
    return ""


async def _a11y_sweep_note(serial: str, run_id: str, lines: list) -> None:
    """Restore a crashed run's accessibility settings; note what happened."""

    with never_raise(logger, "mobile a11y stage: stale sweep skipped", exc_info=True):
        swept = (await a11y.restore_stale(serial, skip_run_id=run_id) or {}).get(
            "content"
        ) or {}
        if swept.get("detail"):
            mark = "ℹ️ " if swept.get("restored") else "⚠️ "
            lines.append(mark + str(swept["detail"])[:300])


async def _a11y_enable_note(serial: str, run_id: str, lines: list) -> None:
    """Enable the fast dump; append a line when it could not be turned on."""

    with never_raise(logger, "mobile a11y stage: enable skipped", exc_info=True):
        on = await a11y.enable(serial, run_id)
        if on.get("error"):
            lines.append(
                "⚠️ Fast screen reading is off for this run ("
                + str(on["error"])[:200]
                + "); using uiautomator, which is slower."
            )


# Notes for ``_mobile_animations_stage`` (moved out of the body to keep it short):
# THE CRASHED RUN'S VALUES FIRST, or our own disable would read 0 and
# the tester's real scales would never come back.
# Notes for ``_mobile_animations_stage`` (moved out of the body to keep it short):
#
#     ``""`` in the ordinary case. Never raises and never stops a run: animations
#     left on make steps slower, not wrong, so a failure is reported and the run
#     carries on.
#
async def _mobile_animations_stage(run_id: str) -> str:
    """Restore a crashed run's animation scales, turn ours off, say what happened."""
    serial = _run_serial(run_id, "mobile animations stage: no serial for the run")
    if not serial:
        return ""
    lines: list = []
    await _anim_sweep_note(serial, run_id, lines)
    await _anim_disable_note(serial, run_id, lines)
    return "\n\n".join(lines)


async def _anim_sweep_note(serial: str, run_id: str, lines: list) -> None:
    """Restore a crashed run's animation scales; note what happened."""

    with never_raise(
        logger, "mobile animations stage: stale sweep skipped", exc_info=True
    ):
        swept = (await animations.restore_stale(serial, skip_run_id=run_id) or {}).get(
            "content"
        ) or {}
        if swept.get("detail"):
            mark = "ℹ️ " if swept.get("restored") else "⚠️ "
            lines.append(mark + str(swept["detail"])[:300])


async def _anim_disable_note(serial: str, run_id: str, lines: list) -> None:
    """Turn animations off; append a line when that failed."""

    with never_raise(logger, "mobile animations stage: disable skipped", exc_info=True):
        off = await animations.disable(serial, run_id)
        if off.get("error"):
            lines.append(
                "⚠️ Could not turn off system animations for this run ("
                + str(off["error"])[:300]
                + "). Steps may be slower to settle; the run continues."
            )
