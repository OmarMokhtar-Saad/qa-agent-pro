"""Turn the fast qa-ime a11y reader on BEFORE App Tester reads the screen."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import _common
from tools.mobile import a11y, a11y_adopt, ime_session, imes_record, run_records

_NOTE = "Turning on the fast screen reader before App Tester."
logger = logging.getLogger(__name__)


def _is_on(result: dict) -> bool:
    content = result.get("content") or {}
    return not result.get("error") and content.get("enabled") is not False


def _why_off(result: dict) -> str:
    """The logged reason the reader stayed off (``""`` when it is on)."""
    content = result.get("content") or {}
    return str(result.get("error") or content.get("reason") or "")[:120]


async def _sweep(serial: str, owner: str) -> None:
    """Give back what a crashed run left, BEFORE this owner records anything: a
    keyboard swept after our own select would be remembered as the tester's."""
    keys = await ime_session.restore_stale(serial, skip_run_id=owner) or {}
    reader = await a11y.restore_stale(serial, skip_run_id=owner) or {}
    for name, swept in (("keyboard", keys), ("a11y", reader)):
        detail = (swept.get("content") or {}).get("detail") or swept.get("error")
        logger.info("early a11y: %s sweep: %s", name, str(detail or "")[:160])


async def _enable(serial: str, owner: str) -> bool:
    # The arm receiver lives only while the QA keyboard is SELECTED: arming under
    # another keyboard answers result=0 and reads as a legacy keyboard. So select
    # it first, remembering the tester's keyboard under the owner (handed over).
    await _sweep(serial, owner)
    ready = await ime_session.ensure_ready(serial, owner)
    if ready.get("error"):
        logger.info("early a11y: keyboard not ready (%s)", str(ready["error"])[:120])
        return False
    on = await a11y.enable(serial, owner)
    logger.info("early a11y: on=%s %s", _is_on(on), _why_off(on))
    return _is_on(on)


async def early_a11y(
    serial: str, owner: str, progress: _common.ProgressCb = None
) -> bool:
    """Sweep stale leftovers, select and arm the QA keyboard, then enable the a11y
    reader under the pre-run *owner*, so the App Tester list is read in
    milliseconds, not by 9-12 s uiautomator dumps. ``False`` (logged, never raised)
    when unavailable: the slow dump stays the fallback. ``hand_over`` moves the
    keyboard and reader records to the run; a call that hands nothing off restores
    both through ``finish_device(owner)``."""
    await _common.emit(progress, _NOTE)
    with never_raise(logger, "early a11y: skipped", exc_info=True):
        return await _enable(serial, owner)
    return False


async def hand_over(owner: str, run_id: str, progress: _common.ProgressCb) -> None:
    """At the hand-off: say so, and give the run the pre-run owner's records."""
    await _common.emit(progress, "Starting the run: first screen next.")
    a11y_adopt.adopt(owner, run_id)
    imes_record.adopt(owner, run_id)
    run_records.drop_empty_prerun(owner)
