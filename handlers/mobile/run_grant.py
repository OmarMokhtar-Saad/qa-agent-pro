"""Part of the mobile_run split (see handlers/mobile/run.py): permission pre-grant."""

from __future__ import annotations

import logging

from handlers.mobile.run_request import _MobileTestRequest
from tools.mobile import adb_grant
from tools.mobile import render as mobile_render

logger = logging.getLogger("handlers.mobile.run")


# Opt-in, and gated on `apply` like `reset_app`: it changes the app's state on
# the device before the first packet, when no screen exists yet for the
# destructive guard to judge. Runs AFTER the reset, because `pm clear` revokes
# every runtime grant. A failed or refused grant never stops the run: the
# permission dialog is still answered the ordinary way when it shows.
async def mobile_grant_stage(req: _MobileTestRequest, serial: str, target: str) -> str:
    """Grant the app's runtime permissions up front; a refusal or ``""``."""
    if not req.grant_permissions or not target:
        return ""
    if not req.apply:
        return mobile_render.apply_refusal(
            "Granting the app's runtime permissions",
            "pm grant of every runtime permission this run's own app requests",
        )
    result = await adb_grant.grant_runtime(serial, target)
    body = result.get("content") or {}
    logger.info(
        "mobile grant_permissions: granted=%d failed=%d error=%s",
        len(body.get("granted") or []),
        len(body.get("failed") or []),
        result.get("error"),
    )
    return ""
