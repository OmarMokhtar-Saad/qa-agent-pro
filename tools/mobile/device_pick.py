"""Pick the device a mobile run drives.

Discovery used to list only ``emulator-*`` serials, so a connected phone was
invisible unless the caller named it. This asks adb for every usable serial and
lets resolve-or-ask decide:

* one device and no request -> that device, and the result says which;
* several devices and no request -> ask, listing each (never the first one);
* a requested serial -> exact match, or "no device matches" (never a stand-in).
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional, Sequence

from tools.mobile import adb
from tools.mobile.resolve import (
    NONE,
    AskCb,
    Candidate,
    Resolution,
    exact_key_match,
    resolve_or_ask,
)

SerialLister = Callable[[], Awaitable[dict]]

_NO_DEVICE = (
    "No device is connected over adb. Ask the user to connect a phone with USB "
    "debugging on, or to start an emulator, then try again."
)


def device_candidates(serials: Sequence[str]) -> list:
    out = []
    for serial in serials:
        kind = "emulator" if str(serial).startswith("emulator-") else "physical device"
        out.append(Candidate(str(serial), "", kind))
    return out


async def pick_device(
    requested: str = "",
    *,
    ask: Optional[AskCb] = None,
    lister: Optional[SerialLister] = None,
) -> Resolution:
    """Resolve ``requested`` (a serial, or empty) against the connected devices."""
    listing = await (lister or adb.devices)()
    if listing.get("error"):
        return Resolution(NONE, question=str(listing["error"]))
    candidates = device_candidates(listing.get("content") or [])
    if not candidates:
        return Resolution(NONE, question=_NO_DEVICE)
    return await resolve_or_ask(
        requested, candidates, noun="device", ask=ask, match=exact_key_match
    )
