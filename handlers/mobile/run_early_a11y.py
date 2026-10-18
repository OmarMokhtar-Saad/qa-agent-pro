"""Turn the fast qa-ime a11y reader on BEFORE App Tester reads the screen."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import _common
from tools.mobile import a11y, a11y_adopt, ime

_NOTE = "Turning on the fast screen reader before App Tester."
logger = logging.getLogger(__name__)


def _is_on(result: dict) -> bool:
    content = result.get("content") or {}
    return not result.get("error") and content.get("enabled") is not False


async def _enable(serial: str, owner: str) -> bool:
    armed = await ime.arm(serial)
    if armed.get("error"):
        logger.info("early a11y: keyboard not armed (%s)", str(armed["error"])[:120])
        return False
    on = await a11y.enable(serial, owner)
    logger.info("early a11y: on=%s %s", _is_on(on), str(on.get("error") or "")[:120])
    return _is_on(on)


async def early_a11y(
    serial: str, owner: str, progress: _common.ProgressCb = None
) -> bool:
    """Arm the installed QA keyboard and enable the a11y reader under the pre-run
    *owner*, so the App Tester list is read in milliseconds, not by 9-12 s
    uiautomator dumps. ``False`` (logged, never raised) when unavailable: the slow
    dump stays the fallback. ``hand_over`` moves the record to the run; a call
    that hands nothing off restores it through ``finish_device(owner)``, whose
    provisioning-owner branch calls ``a11y.restore`` (a no-op without a record)."""
    await _common.emit(progress, _NOTE)
    with never_raise(logger, "early a11y: skipped", exc_info=True):
        return await _enable(serial, owner)
    return False


async def hand_over(owner: str, run_id: str, progress: _common.ProgressCb) -> None:
    """At the hand-off: say so, and give the run the reader the pre-run owner armed."""
    await _common.emit(progress, "Starting the run: first screen next.")
    a11y_adopt.adopt(owner, run_id)
