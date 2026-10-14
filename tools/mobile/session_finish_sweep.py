"""Mobile session: restore what earlier crashed runs left. Cut from tools/mobile/session_finish.py (w17 sessA)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from config.settings_guard import never_raise
from tools.mobile import a11y, animations, ime_session

logger = logging.getLogger("tools.mobile.session")


def _content(reply: Any) -> dict[str, Any]:
    """The ``content`` of a restore reply, or ``{}``."""
    return (reply or {}).get("content") or {}


# AND ANY KEYBOARD AN EARLIER RUN CRASHED OUT OF. Done here as well as at
# run start because the two reach different machines: run start covers the
# tester who runs the lane again, this covers the run that is ending on a
# device whose previous holder died. Both go through the same function, so
# neither can drift from the held-device check that makes it safe.
#
# ``serial_fn`` is ``serial_of``, THE producer of a run's device for the
# locking path, passed in by the facade rather than a second derivation off
# the restore envelope, which does not carry one.
async def sweep_stale(
    serial_fn: Callable[[str], str], owner: str
) -> tuple[dict, dict, dict]:
    """Restore what earlier crashed runs left: ``(ime, animations, a11y)``."""

    a11y_stale: dict[str, Any] = {}
    anim_stale: dict[str, Any] = {}
    swept: dict[str, Any] = {}
    skip = {"skip_run_id": str(owner)}
    with never_raise(
        logger, "mobile.session.finish_device: stale sweep skipped", exc_info=True
    ):
        serial = serial_fn(str(owner))
        if serial:
            swept = _content(await ime_session.restore_stale(serial, **skip))
            anim_stale = _content(await animations.restore_stale(serial, **skip))
            a11y_stale = _content(await a11y.restore_stale(serial, **skip))
    return swept, anim_stale, a11y_stale
