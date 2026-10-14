"""Make the QA IME the active keyboard for a run, once, within a bound."""

from __future__ import annotations

import asyncio
import logging

from tools.mobile import ime, imes_record, imes_remember, imes_select, imes_state

logger = logging.getLogger("tools.mobile.ime_session")

#: How long the whole ensure sequence may take before it is reported as failed.
#:
#: It is an install plus three shell round trips on a device that may be
#: booting. The constraint is a TESTER's patience at the first type op of a run,
#: not the device's speed: past this the honest answer is "typing is not ready",
#: because a run that hangs here looks to a tester exactly like a run that hung
#: in their app.
IME_READY_TIMEOUT_S = 120.0


def _timeout_error(serial: str) -> dict:
    """The refusal naming the device when the ensure sequence ran out of time."""
    return {
        "error": (
            "The QA input method did not become ready on "
            + str(serial)[:64]
            + " within "
            + str(int(IME_READY_TIMEOUT_S))
            + "s."
        ),
        "content": None,
    }


async def _bounded(serial: str, run_id: str) -> dict:
    """``_ensure_ready`` under the ``IME_READY_TIMEOUT_S`` bound."""
    try:
        return await asyncio.wait_for(
            _ensure_ready(serial, run_id), timeout=IME_READY_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        return _timeout_error(serial)


async def _armed(serial: str, result: dict) -> dict:
    """*result* once the keyboard is armed, else the arm error."""
    # G4: arm-once nonce. A keyboard that cannot be armed (armed by a crashed
    # run, a refused ARM, or an unexpected ARM reply) is a keyboard that is not
    # ready: the run's one sticky fallback to `input text` applies, with the usual notice.
    armed = await ime.arm(serial)
    if armed.get("error"):
        return {"error": str(armed["error"]), "content": None}
    return result


# Make the QA IME the active keyboard for this run, once, within a bound.
#
# Bounded HERE rather than by the caller: this is an install plus three shell
# round trips on a device that may still be booting, and a caller that forgot
# the timeout would hang with nothing to stop it. A timeout is reported as a
# refusal naming the device, never as a success -- typing into a keyboard that
# never became active is the exact silence this whole change exists to end.
async def ensure_ready(serial: str, run_id: str) -> dict:
    """Make the QA IME the active keyboard for this run, once, within a bound."""
    # A failure is decided ONCE per run: every later case that types is told the
    # same thing at once instead of retrying a 120 s install (R1d). The tester's
    # keyboard is untouched by a failed install, and the existing restore path
    # (ime.json) still runs at the end of the run.
    known = imes_record.read_fallback(run_id)
    if known.get("error"):
        return {"error": str(known["error"]), "content": None}
    result = await _bounded(serial, run_id)
    if not result.get("error"):
        result = await _armed(serial, result)
    if result.get("error"):
        imes_record.write_fallback(run_id, str(result["error"]))
    return result


def _already_ready(body: dict) -> bool:
    """True when the keyboard is installed, selected and not known to be off-pin."""
    return bool(
        body.get("installed")
        and body.get("selected")
        and body.get("pinned") is not False
    )


def _ready_reply() -> dict:
    """The no-op answer for a device already installed-and-selected."""
    return {
        "error": None,
        "content": {
            "ready": True,
            "changed": False,
            "detail": "the QA input method was already installed and selected",
        },
    }


def _needs_install(body: dict) -> bool:
    """True when the keyboard is missing or not the pinned build."""
    return not body.get("installed") or body.get("pinned") is False


async def _prepare(serial: str, run_id: str, body: dict) -> dict | None:
    """Remember the tester's keyboard, then install if needed; an error, else ``None``."""
    # REMEMBER FIRST. Everything below this line changes the device.
    refused = await imes_remember._remember_keyboard(serial, run_id)
    if refused:
        return refused
    if _needs_install(body):
        installed = await ime.install(serial)
        if installed.get("error"):
            return installed
    return None


# The sequence itself. See :func:`ensure_ready` for the bound.
#
# Idempotent: a device already installed-and-selected is reported as ready
# and nothing is written, installed, enabled or selected. It is NOT free --
# it costs the adb reads `state()` makes: the package list and the active input
# method, plus `pm path` and `sha256sum` when the keyboard is installed and the
# manifest pins a hash. Worth stating precisely because the per-case fallback
# calls this on every case that types, and "no-op" would be a wider claim than
# the reads it actually makes.
#
# Otherwise the tester's current keyboard
# is remembered FIRST -- before anything is installed, enabled or selected --
# because a remember that runs after the change records our own keyboard as
# the thing to restore, which is how a tester's phone keeps a keyboard they
# never chose.
async def _ensure_ready(serial: str, run_id: str) -> dict:
    """The sequence itself. See :func:`ensure_ready` for the bound."""
    try:
        now = await imes_state.state(serial)
        if now.get("error"):
            return now
        body = now.get("content") or {}
        if _already_ready(body):
            return _ready_reply()
        refused = await _prepare(serial, run_id, body)
        if refused:
            return refused
        return await imes_select._select_ours(serial)
    except Exception as exc:
        logger.exception("mobile.ime_session.ensure_ready failed")
        return {"error": str(exc), "content": None}
