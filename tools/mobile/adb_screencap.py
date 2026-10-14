"""Screen capture as PNG: ``screencap`` and its verdict helpers (moved from adb.py)."""

from __future__ import annotations

import asyncio
import logging

from tools import tool_status
from tools.mobile import adb_run
from tools.mobile.adb_host import host
from tools.mobile.adb_reply import clamp_timeout, refuse_device_id

logger = logging.getLogger("tools.mobile.adb")


def _adb_missing() -> dict:
    """The reply when no adb binary can be found for a capture."""
    return {
        "error": "adb was not found, so no screen could be captured.",
        "content": None,
    }


# Never raises, and a failure is never a partial image: no adb, a timeout, a
# non-zero exit, an over-cap payload and a body that is not a PNG all come
# back with ``content: None`` and a stated error, so the caller can carry on
# with the dump alone.
#
# **It calls :func:`run_argv` DIRECTLY, and that is forced rather than
# chosen.** ``raw`` decodes stdout with ``errors="replace"``, so a PNG routed
# through ``raw`` / ``device`` / ``shell`` could never be re-encoded.
# ``run_argv`` is still the module's SINGLE subprocess seam. The serial check
# ``device`` would have done is done here with the same wording.
# ``exec-out`` rather than ``shell`` for the reason :func:`run_as_cat` gives.
# The timeout is the module DEFAULT, whose CEILINGS row already states the
# constraint that applies.
async def screencap(serial: str, *, timeout_s: float | None = None) -> dict:
    """The current screen as PNG BYTES: ``{"error", "content": bytes|None}``."""
    if not host()._valid_device_id(serial):
        return refuse_device_id(serial)
    try:
        # A caller with less time left passes a shorter wait (fix round 3,
        # item 3): never above the default, never below one second.
        wait_s = clamp_timeout(timeout_s)
        cmd = [host().resolve_adb(), "-s", str(serial), "exec-out", "screencap", "-p"]
        rc, out, err = await adb_run.run_argv(cmd, wait_s)
    except FileNotFoundError:
        return _adb_missing()
    except asyncio.TimeoutError:
        return _screencap_timed_out(wait_s)
    except Exception as exc:
        logger.exception("mobile.adb.screencap failed")
        return {"error": str(exc), "content": None}
    return _screencap_verdict(rc, out, err)


def _screencap_timed_out(wait_s: float) -> dict:
    """A TIMEOUT, said as one (fix round 3, item 3). The Air run's final
    screencap timed out after 30 s and the call was logged `ok`: this branch
    returned the same shape as a refusal. `timed_out` is the same additive field
    `raw` sets, and the mark reaches `_tracked`.
    """
    tool_status.mark_timed_out("screencap")
    return {
        "error": ("screencap did not answer within " + str(int(wait_s)) + "s."),
        "timed_out": True,
        "content": None,
    }


def _exited(rc: int, err: bytes) -> dict:
    """The refusal for a ``screencap -p`` that exited non-zero, in the device's words."""
    # The device's own words, like `uiautomator_dump` quotes `cat`'s.
    said = " ".join(err.decode(errors="replace").split())[:200]
    return {
        "error": ("screencap exited " + str(rc) + ((": " + said) if said else ".")),
        "content": None,
    }


def _not_png(out: bytes) -> dict:
    """The refusal for a body that is not a PNG (a secure window blocks a capture)."""
    said = " ".join(out[:200].decode(errors="replace").split())[:200]
    return {
        "error": (
            "screencap returned no PNG for this screen. A secure window (a "
            "password field or a payment sheet) blocks a capture."
            + ((" The device said: " + said) if said else "")
        ),
        "content": None,
    }


def _screencap_verdict(rc: int, out: bytes, err: bytes) -> dict:
    """Turn a finished ``screencap -p`` into the result dict: a refusal for a
    non-zero exit, an over-cap body or a body that is not a PNG."""
    if rc != 0:
        # Checked BEFORE the magic below so a refusal is reported as a refusal
        # rather than as "that was not a PNG".
        return _exited(rc, err)
    if len(out) > host().MAX_SCREENSHOT_BYTES:
        return {
            "error": (
                "This screen's PNG exceeds the "
                + str(host().MAX_SCREENSHOT_BYTES)
                + " byte cap and was discarded."
            ),
            "content": None,
        }
    if not out.startswith(host()._PNG_MAGIC):
        return _not_png(out)
    return {"error": None, "content": out}
