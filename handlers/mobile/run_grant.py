"""Part of the mobile_run split (see handlers/mobile/run.py): permission pre-grant."""

from __future__ import annotations

import logging

from handlers.mobile.run_request import _MobileTestRequest
from tools.mobile import adb_grant
from tools.mobile import render as mobile_render
from tools.mobile.perm_groups import parse_deny, perms_of

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
    denied = frozenset(perms_of(parse_deny(getattr(req, "deny_permissions", None))[0]))
    result = await adb_grant.grant_runtime(serial, target, denied)
    _log("grant", result)
    return await _revoke_stage(serial, target, denied) if denied else ""


async def _revoke_stage(serial: str, target: str, denied: frozenset) -> str:
    """Take the denied permissions away; a refusal when the revoke itself failed.

    A denied permission that is still granted would make the run's deny a lie,
    so an adb error stops the run. A per-permission failure (a name the app never
    declared) stays a log line."""
    result = await adb_grant.revoke_runtime(serial, target, sorted(denied))
    _log("revoke", result)
    if result.get("error"):
        return (
            "Not started: could not revoke the denied permissions (%s)."
            % (str(result["error"])[:200])
        )
    return ""


def _log(what: str, result: dict) -> None:
    body = result.get("content") or {}
    logger.info(
        "mobile %s_permissions: done=%d failed=%d error=%s",
        what,
        len(body.get("granted") or body.get("revoked") or []),
        len(body.get("failed") or []),
        result.get("error"),
    )
