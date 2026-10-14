"""Part of the mobile_run split (see handlers/mobile/run.py): locale."""

from __future__ import annotations

from dataclasses import dataclass

import tools.mobile.session_records as session_records
from tools.mobile import adb as mobile_adb
from tools.mobile import render as mobile_render
from tools.untrusted import wrap_untrusted


# Notes for ``_mobile_locale_stage`` (moved out of the body to keep it short):
#
#     Three outcomes:
#
#     * nothing requested -> empty markdown, and a record saying what the device
#       was already in, so the report can still state the language;
#     * requested and confirmed -> empty markdown and the record;
#     * requested and NOT confirmed -> a refusal that NAMES what the device is
#       actually in and the two ways to fix it. This is the ORDINARY outcome on an
#       emulator the tester started themselves: `persist.sys.locale` is a root
#       property on the `google_apis_playstore` user build, so the write does not
#       land, and letting the run proceed would produce a report headed with a
#       language the screenshots are not in.
#
#     Changing the device's language is an effect on the tester's device, so a
#     REQUEST needs `apply=true`, exactly as an install does. A call with no
#     `locale` touches nothing and needs nothing.
#
async def _mobile_locale_stage(serial: str, locale: str, apply: bool) -> tuple:
    """Put the device into the requested language. ``(markdown_or_empty, record)``."""

    wanted = str(locale or "").strip()
    if wanted and not apply:
        return _locale_apply_refusal(wanted), None
    applied = await session_records.apply_locale(serial, wanted)
    if applied.get("error"):
        return "\u26a0\ufe0f " + str(applied["error"])[:300], None
    record = applied.get("content") or {}
    if wanted and not record.get("matched"):
        return _locale_mismatch_block(wanted, record), None
    return "", record


def _locale_apply_refusal(wanted: str) -> str:
    """The refusal for a language request that came without ``apply``."""

    return mobile_render.apply_refusal(
        "Setting the device language to " + wanted,
        "one property write on the emulator",
    )


_MISMATCH_TAIL = (
    " over screens in another language is worse than no run at "
    "all.\n\nTwo ways forward:\n\n"
    "1. Let the lane boot its own emulator with the language set from "
    "the first frame -- call `qa_mobile_test` with `locale` and no "
    "`serial`, with no emulator already running. That is the only "
    "mechanism that works without root.\n"
    "2. Set it by hand on this emulator (Settings > System > "
    "Languages) and call `qa_mobile_test` again."
)


def _locale_mismatch_block(wanted: str, record: dict) -> str:
    """The refusal naming the language the device is actually in."""
    actual = str(record.get("actual") or "")
    detail = str(record.get("detail") or "")
    return (
        "## The emulator is not in "
        + wanted
        + "\n\nIt is in `"
        + (actual or "a language it would not report")
        + "`"
        + ((" (" + detail + ")") if detail else "")
        + ". Nothing was run: a report headed "
        + wanted
        + _MISMATCH_TAIL
    )


@dataclass(frozen=True)
class _ResetCall:
    """The four inputs of one app-reset stage."""

    serial: str
    package: str
    reset_app: bool
    apply: bool


# Notes for ``_mobile_reset_app_stage`` (moved out of the body to keep it short):
#
#     ``(markdown_or_empty, record_or_none)`` -- the same shape
#     `_mobile_locale_stage`/`_mobile_capture_stage` return, and gated on
#     `apply` alone for the same reason: this runs after `_mobile_app_stage`
#     confirms `package` but still before `session.preflight_for`, so no
#     device screen exists yet for the destructive guard to judge. There is no
#     "actuated node" here any more than there is for locale or capture, so
#     `apply=true` plus the confirmed, run-scoped `package` IS the guard
#     equivalent at this point in the flow -- exactly as it already is for
#     those two stages. `qa_submit_mobile_step`'s `clear_app_data` op is the
#     SAME primitive (`adb.clear_app_data` then `adb.launch`), guarded by the
#     ordinary destructive-guard check instead, because a script action runs
#     against a device that already has a screen to judge it against.
#
#     A clear failure returns a refusal here and nothing else: the caller must
#     return this stage's markdown straight back and never reach
#     `preflight_for`, so a run is never planned against an app this call
#     could not actually reset.
#
async def _mobile_reset_app_stage(call: _ResetCall) -> tuple:
    """Wipe THIS run's own app before its first packet."""
    if not call.reset_app:
        return "", None
    if not call.apply:
        return _reset_apply_refusal(), None
    return await _reset_run(call.serial, call.package)


def _reset_apply_refusal() -> str:
    """The refusal for an app reset that came without ``apply``."""

    return mobile_render.apply_refusal(
        "Resetting the app's data",
        "pm clear on this run's own app, then relaunching it",
    )


async def _reset_run(serial: str, package: str) -> tuple:
    """Clear the app's data and relaunch it; a clear failure is a refusal."""

    cleared = await mobile_adb.clear_app_data(serial, package)
    if cleared.get("error"):
        return (
            "\u26a0\ufe0f Could not reset the app's data: "
            + wrap_untrusted("device_pm_clear", str(cleared["error"]), limit=400),
            None,
        )
    await mobile_adb.launch(serial, package)
    return "", {"requested": True, "package": str(package or ""), "cleared": True}
