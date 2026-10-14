"""Bounded waits and polls while the QA IME becomes the active keyboard."""

from __future__ import annotations

import asyncio
import logging

from tools.mobile import ime, imes_state

logger = logging.getLogger("tools.mobile.ime_session")

#: The LONGEST pause between one failed enable+select attempt and the next.
#: It is a bound, not a sleep (S17): :func:`_await_listed` ends it as soon as
#: the device lists the QA keyboard.
IME_SELECT_RETRY_DELAY_S = 1.0

#: How often :func:`_await_listed` asks the device for its keyboards.
IME_RETRY_POLL_S = 0.25

#: Reads of `state()` after a successful enable+select before treating the
#: keyboard as stuck. A single read cannot distinguish "refused" from "not yet
#: settled" -- this module's own docstring already says the device layer
#: answers asynchronously.
IME_POLL_MAX_ATTEMPTS = 5

#: Delay between one poll read and the next.
IME_POLL_INTERVAL_S = 1.0


async def sleep(seconds: float) -> None:
    """``asyncio.sleep`` under its own name, so a test can monkeypatch retry
    and poll delays to zero without touching the shared ``asyncio`` module."""
    await asyncio.sleep(seconds)


async def _await_listed(serial: str) -> None:
    """Pause before the next enable+select attempt until the device lists the
    QA keyboard, at most :data:`IME_SELECT_RETRY_DELAY_S` (S17: no fixed
    waits). An install is registered asynchronously; the fixed second this
    replaced was spent in full even when the keyboard was listed at once."""
    left = IME_SELECT_RETRY_DELAY_S
    while left > 0:
        step = min(IME_RETRY_POLL_S, left)
        await sleep(step)
        left -= step
        if not (await ime.resolve_installed_id(serial)).get("error"):
            return


def _selected_reply() -> dict:
    """The answer once our keyboard is active."""
    return {
        "error": None,
        "content": {
            "ready": True,
            "changed": True,
            "detail": "selected the QA input method",
        },
    }


async def _poll_selected(serial: str) -> tuple[dict | None, dict]:
    """Poll until our keyboard is active: ``(result_or_None, last_settled_state)``."""
    settled: dict = {}
    for poll in range(IME_POLL_MAX_ATTEMPTS):
        after = await imes_state.state(serial)
        if after.get("error"):
            return after, settled
        settled = after.get("content") or {}
        if settled.get("selected"):
            return _selected_reply(), settled
        if poll < IME_POLL_MAX_ATTEMPTS - 1:
            await sleep(IME_POLL_INTERVAL_S)
    return None, settled
