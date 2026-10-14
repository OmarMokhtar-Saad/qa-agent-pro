"""The QA IME's state on one device: installed, selected, pinned."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.mobile import ime

logger = logging.getLogger("tools.mobile.ime_session")


class _Reads(NamedTuple):
    """What the three device reads behind ``state`` found."""

    ours: str
    installed: bool
    now: str


async def _pinned_state(serial: str, installed: bool) -> bool | None:
    """True/False for an installed package's build pin; None when not installed."""
    # Only an installed package has a build to compare; None is never a match.
    if not installed:
        return None
    verdict = await ime.verify_pinned(serial)
    return (verdict.get("content") or {}).get("pinned")


async def _gather(serial: str) -> dict | _Reads:
    """The manifest, install and current-keyboard reads, or the first error."""
    resolved = ime.manifest()
    if resolved.get("error"):
        return resolved
    ours = str((resolved["content"] or {}).get("ime_id") or "")
    present = await ime.installed(serial)
    if present.get("error"):
        return present
    current = await ime.current_ime(serial)
    if current.get("error"):
        return current
    installed = bool((present.get("content") or {}).get("installed"))
    return _Reads(ours, installed, str(current.get("content") or ""))


def _content(reads: _Reads, pinned: bool | None) -> dict:
    """The ``state`` answer body."""
    return {
        "installed": reads.installed,
        "pinned": pinned,
        # By COMPONENT IDENTITY, not spelling. A device stores the
        # shorthand `pkg/.Class` while the manifest pins the expanded
        # form, and `==` answers "that is not our keyboard" about our
        # own keyboard -- the defect `ime.same_component` exists for.
        "selected": ime.same_component(reads.now, reads.ours),
        "current": reads.now,
    }


async def state(serial: str) -> dict:
    """``{"installed", "selected", "current", "pinned"}`` for one device.

    ``pinned`` is True/False/None; None is not determined, or not installed.
    Never raises.
    """
    try:
        reads = await _gather(serial)
        if not isinstance(reads, _Reads):
            return reads
        pinned = await _pinned_state(serial, reads.installed)
        return {"error": None, "content": _content(reads, pinned)}
    except Exception as exc:
        logger.exception("mobile.ime_session.state failed")
        return {"error": str(exc), "content": None}
