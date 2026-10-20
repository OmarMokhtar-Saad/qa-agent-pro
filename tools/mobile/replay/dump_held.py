"""The held dump for the explore packet: young, or older but proven by the focus probe."""

from __future__ import annotations

import asyncio
import re

from tools.mobile import dump_reuse
from tools.mobile.replay import dump_focus

#: Seconds an older held dump may still stand when the live focus agrees with it.
#: Nothing actuated on the device since it was read (the generation check), and
#: the focused window still belongs to the app it was read from.
PROBED_MAX_AGE_S = 20.0

#: Seconds the focus probe may take; far cheaper than the dump it saves.
PROBE_S = 3.0

_ROOT_PACKAGE = re.compile(r'<node\b[^>]*?\bpackage="([^"]*)"')


def _dump_package(result: dict) -> str:
    """The package of the first node of a raw dump, or ``""``."""
    found = _ROOT_PACKAGE.search(str(result.get("content") or ""))
    return found.group(1) if found else ""


async def _focused_package(serial: str) -> str:
    """The focused package, or ``""`` when not known within the probe budget."""
    try:
        probed = await asyncio.wait_for(
            dump_focus.current(serial, timeout=int(PROBE_S) + 1), PROBE_S
        )
    except (TimeoutError, OSError, ValueError, KeyError):
        return ""
    return probed.split("/", 1)[0]


async def take_held(serial: str) -> dict | None:
    """The replay's last dump when it is provably current; once. ``None`` otherwise."""
    held = dump_reuse.take_after_refusal(serial)
    if held is not None:
        return held
    aged = dump_reuse.take_aged(serial)
    if aged is None:
        return None
    result, age = aged
    if age <= dump_reuse.REUSE_MAX_AGE_S:
        return result
    if age > PROBED_MAX_AGE_S:
        return None
    package = _dump_package(result)
    if package and package == await _focused_package(serial):
        return result
    return None
