"""Arming the fast reader while a system dialog hides every app window.

Measured: on the device runs of 2026-10-17 the notification-permission dialog was in
front while the service connected, so every connect poll answered ``no_window``,
arming gave up after ten polls and the whole run read with uiautomator. ``no_window``
is the CONNECTED service saying nothing readable is in front, so arming stops polling
on it, registers the provider at once and runs the arming parity check (the same
:func:`tools.mobile.a11y_parity.compare` against uiautomator) on the FIRST readable
tree instead. Until that check passes nothing fast is served: a mismatch or a failed
uiautomator read restores the setting and drops the provider, as an eager parity
failure would, and that dump falls back to uiautomator.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from tools.mobile import step_timing
from tools.mobile.a11y_confirm import uiautomator_aside
from tools.mobile.a11y_parity import compare
from tools.mobile.a11y_restore import restore

Provider = Callable[[str], Awaitable[dict]]

#: The refusal code of a connected service with no readable window in front.
NO_WINDOW = "no_window"
#: The dump-latency bucket of the arming parity dump.
PARITY_SOURCE = "a11y-parity"
#: Ledger notes: arming deferred, then the first readable tree checked.
DEFERRED_NOTE = "a11y_parity_deferred"
CHECKED_NOTE = "a11y_parity_first_window"
#: The refusal served when the deferred parity check fails (the provider is gone).
FAILED_REFUSAL = "refused: a11y parity failed on the first readable window"


def hidden_window(got: dict) -> bool:
    """True when *got* is the connected service's ``no_window`` refusal."""
    return NO_WINDOW in str(got.get("error") or "")


async def _parity(serial: str, xml: str) -> dict:
    """The arming check: *xml* against a fresh uiautomator read of the same screen."""
    slow = await uiautomator_aside(serial, PARITY_SOURCE)
    if slow.get("error"):
        return slow
    return compare(xml, str(slow.get("content") or ""))


#: Pre-run owner -> the run its record was handed to (``a11y_adopt.adopt``): a
#: deferred check that fails after the hand-off restores the run's record.
_HANDED: dict[str, str] = {}


def handed_to(owner: str, run_id: str) -> None:
    _HANDED[str(owner)] = str(run_id)


def _holder(run_id: str) -> str:
    return _HANDED.pop(run_id, run_id)


def deferred(inner: Provider, run_id: str) -> Provider:
    """*inner*, holding back every tree until the first readable one passes parity."""
    step_timing.note(DEFERRED_NOTE)
    pending = {"run_id": str(run_id)}

    async def provider(serial: str) -> dict:
        got = await inner(serial)
        if not pending or got.get("error") or not got.get("content"):
            return got
        checked = await _parity(str(serial), str(got["content"]))
        if checked.get("error"):
            await restore(_holder(pending["run_id"]))
            why = str(checked["error"])[:160]
            return {"error": FAILED_REFUSAL + " (" + why + ")", "content": None}
        _holder(pending["run_id"])
        pending.clear()
        step_timing.note(CHECKED_NOTE)
        return got

    return provider
