"""Select the QA IME with bounded retries."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.mobile import ime, imes_poll, imes_state
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mobile.ime_session")

#: Attempts of enable+select before ``_ensure_ready`` gives up. Bounded because
#: an ``ime`` command that refuses once (a stale receiver, a device still
#: settling) is not evidence it will refuse forever, and Android's own answer
#: is known to arrive asynchronously -- see the shorthand/expanded id note on
#: `ime.same_component`.
IME_SELECT_MAX_ATTEMPTS = 3

#: Worst case: IME_SELECT_MAX_ATTEMPTS * (IME_SELECT_RETRY_DELAY_S +
#: IME_POLL_MAX_ATTEMPTS * IME_POLL_INTERVAL_S) = 3 * (1 + 5) = 18s, well
#: inside the unchanged IME_READY_TIMEOUT_S outer bound.


class _Outcome(NamedTuple):
    """How the bounded enable+select attempts ended."""

    early: dict | None
    settled: dict
    failure: str
    reached_poll: bool


async def _enable_and_select(serial: str) -> str:
    """Enable then select our keyboard; the failure text, or ``""`` on success."""
    enabled = await ime.enable(serial)
    if enabled.get("error"):
        return str(enabled.get("error") or "")
    chosen = await ime.select(serial)
    if chosen.get("error"):
        return str(chosen.get("error") or "")
    return ""


async def _try_all(serial: str) -> _Outcome:
    """Run the bounded attempts; ``early`` is the result once one sticks."""
    settled: dict = {}
    failure = ""
    reached_poll = False
    for attempt in range(1, IME_SELECT_MAX_ATTEMPTS + 1):
        failure = await _enable_and_select(serial)
        if not failure:
            reached_poll = True
            early, settled = await imes_poll._poll_selected(serial)
            if early:
                return _Outcome(early, settled, failure, reached_poll)
        if attempt < IME_SELECT_MAX_ATTEMPTS:
            await imes_poll._await_listed(serial)
    return _Outcome(None, settled, failure, reached_poll)


def _stuck_error(settled: dict, failure: str) -> dict:
    """The error answer when our keyboard never became the active one."""
    detail = wrap_untrusted(
        "device_ime_state",
        str(settled.get("current") or "no keyboard")[:120]
        + ("; last attempt failed: " + failure[:200] if failure else ""),
    )
    return {
        "error": (
            "The QA input method was installed and enabled but did not "
            "become the active keyboard; typing would go to "
            + detail
            + ". Nothing was typed."
        ),
        "content": None,
    }


async def _select_ours(serial: str) -> dict:
    """Select our keyboard with bounded retries; the error result if it never sticks."""
    out = await _try_all(serial)
    if out.early:
        return out.early
    settled = out.settled
    if not out.reached_poll:
        after = await imes_state.state(serial)
        if after.get("error"):
            return after
        settled = after.get("content") or {}
    return _stuck_error(settled, out.failure)
