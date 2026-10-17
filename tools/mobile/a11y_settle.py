"""The arming parity check on a settled screen.

Measured: on the device runs of 2026-10-17 (2 of 12 a11y runs) the eager arming check
refused with "share too few nodes" on a freshly reset app that was still loading: the
fast dump and the uiautomator dump (~2.3 s on that device) were of two different frames.
So the check reads fast, then uiautomator, then fast again. When the two fast dumps
describe different nodes the screen moved during the uiautomator read, and the check
runs again on the newer frame, up to :data:`SETTLE_MAX_ROUNDS` rounds. A refusal on a
screen that did not move is final, so a real mismatch still turns the reader off.
"""

from __future__ import annotations

from tools.mobile import a11y_dump
from tools.mobile.a11y_parity import compare, node_digest
from tools.mobile.replay import dump as replay_dump

#: Rounds of fast / uiautomator / fast reads before a parity refusal is final.
SETTLE_MAX_ROUNDS = 3


async def fast_read(serial: str) -> dict:
    return await a11y_dump.fetch_dump(serial)


async def slow_read(serial: str) -> dict:
    return await replay_dump.dump_raw(serial, use_provider=False)


async def _moved_to(serial: str, fast_xml: str) -> str:
    """The newer fast dump when the screen moved since *fast_xml*, else ``""``."""
    after = await fast_read(serial)
    if after.get("error") or not after.get("content"):
        return ""
    newer = str(after["content"])
    return newer if node_digest(newer) != node_digest(fast_xml) else ""


async def checked(serial: str, fast_xml: str) -> dict:
    """Compare *fast_xml* with uiautomator, re-reading while the screen still moves."""
    got: dict = {"error": "a11y parity check: no round ran", "content": None}
    for _ in range(SETTLE_MAX_ROUNDS):
        slow = await slow_read(serial)
        if slow.get("error"):
            return slow
        got = compare(fast_xml, str(slow["content"]))
        if not got.get("error"):
            return got
        fast_xml = await _moved_to(serial, fast_xml)
        if not fast_xml:
            return got
    return got
