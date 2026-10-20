"""The uiautomator read both a11y checks make from INSIDE the a11y provider.

It is booked as an aside in the dump-latency state, so the time lands in its own
bucket and not in the ``qa-ime-a11y`` sample of the dump that is being served. It
calls ``adb.uiautomator_dump`` directly: the replay ``dump_raw`` would first join the
in-flight dump task, which is the one running this very provider.
"""

from __future__ import annotations

import asyncio
import time

from tools.mobile import adb, dump_latency_state


async def uiautomator_aside(serial: str, label: str, cap: float | None = None) -> dict:
    """A fresh uiautomator dump of *serial*, its time recorded under *label*.

    Past *cap* seconds the answer is an error: the caller treats it as unchecked.
    """
    started = time.monotonic()
    try:
        slow = await asyncio.wait_for(
            adb.uiautomator_dump(serial, use_provider=False), cap
        )
    except TimeoutError:
        slow = {"error": f"uiautomator dump over {cap} s", "content": None}
    dump_latency_state.record_aside(serial, time.monotonic() - started, label)
    return slow
