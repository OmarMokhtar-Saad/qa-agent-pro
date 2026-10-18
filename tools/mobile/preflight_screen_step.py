"""The preflight step that records the device screen evidence."""

from __future__ import annotations

from tools.mobile import preflight_rows as rows
from tools.mobile import preflight_screen
from tools.mobile.preflight_consts import _STEP_ERRORS
from tools.mobile.preflight_rows import _Dev, _failed

NO_DEVICE = "cannot check without a device"


async def _step_device_screen(checks: list[dict], target: str, dev: _Dev) -> None:
    """Lock state, foreground package, a dialog in front."""
    if not dev.serial:
        checks.append(
            rows.record_row(
                "device_screen",
                rows._Row(False, NO_DEVICE, "Start the emulator first."),
            )
        )
        return
    try:
        checks.append(await preflight_screen.screen_row(dev.serial, str(target or "")))
    except _STEP_ERRORS as exc:
        checks.append(_failed("device_screen", exc))
