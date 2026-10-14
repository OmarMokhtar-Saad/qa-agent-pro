"""``adb <args>`` with no device selection: ``raw`` (moved from adb.py)."""

from __future__ import annotations

import asyncio
import logging

from tools.mobile import adb_run
from tools.mobile.adb_consts import DEFAULT_TIMEOUT_S
from tools.mobile.adb_host import host

logger = logging.getLogger("tools.mobile.adb")


def _ran(rc: int, out: bytes, err: bytes) -> dict:
    return {
        "error": None,
        "content": {
            "rc": rc,
            "out": out.decode(errors="replace"),
            "err": err.decode(errors="replace"),
        },
    }


def _not_found() -> dict:
    return {
        "error": ("adb was not found. Install Android Studio and try again."),
        "content": None,
    }


def _timed_out(timeout: float) -> dict:
    # `timed_out` is ADDITIVE and exists because "adb did not answer" is
    # not always the finding. A caller that gave a probe a budget sized to
    # the thing being probed -- `preflight`'s DNS check -- needs to tell an
    # OVERRUN apart from adb being unusable, and the two arrive here as the
    # same `error` string. Substring-matching that prose from another module
    # would make the wording load-bearing, so the fact is stated as a field.
    # Every OTHER consumer is unchanged and correct to ignore it: `error` is
    # still set and still says what they need. `preflight` is the one reader
    # that branches on it.
    return {
        "error": "adb did not answer within " + str(timeout) + "s.",
        "timed_out": True,
        "content": None,
    }


async def raw(
    args: list[str],
    timeout: int = DEFAULT_TIMEOUT_S,
    stdin_data: bytes | None = None,
) -> dict:
    """Run ``adb <args>`` with no device selection. Never raises."""
    try:
        cmd = [host().resolve_adb()] + [str(a) for a in args]
        rc, out, err = await adb_run.run_argv(cmd, timeout, stdin_data)
        return _ran(rc, out, err)
    except FileNotFoundError:
        return _not_found()
    except asyncio.TimeoutError:
        return _timed_out(timeout)
    except Exception as exc:
        logger.exception("mobile.adb.raw failed")
        return {"error": str(exc), "content": None}
