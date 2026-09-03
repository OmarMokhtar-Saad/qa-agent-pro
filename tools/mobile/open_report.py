"""Open a rendered report with the OS opener, and NEVER raise.

The handler returns the report path whether or not this succeeds, so every
failure here is CONTENT (``opened: False`` plus a reason), not an error: no
opener installed, a headless box, an unexpected platform, an opener that dies.
A tester who cannot open the file automatically still has the path.

**The kill-switch is read in the innermost public function that performs the
effect**, which is this one -- ``subprocess.Popen(`` is in
``tests/mobile/test_mobile_killswitch_surface.py``'s ``EFFECT_CALLS``, so that
invariant test would name this module by line otherwise. It is not added to
``EXEMPT``: there is nothing exceptional about opening a browser, and an
exemption list that absorbs its first real case stops being a list of
exceptions.

**Nothing is waited on.** The child is fire-and-forget with all three streams on
``DEVNULL`` and its own session, so the house rule about a child process
without a timeout is satisfied by there being no wait to time out -- a report
viewer outlives the tool call by design.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

FLAG_NAME = "QA_MOBILE_RUN_ENABLED"

FLAG_REFUSAL = (
    "The report was not opened. The mobile lane needs `"
    + FLAG_NAME
    + "=true` in `.env` and an MCP server restart."
)

NO_OPENER = "no file opener on this machine"

WINDOWS = "startfile"


def _opener() -> str:
    """The opener for this platform, or ``""``.

    Private, so the kill-switch invariant test skips it -- and correctly: it
    resolves a path and performs no effect.
    """
    if sys.platform == "darwin":
        return shutil.which("open") or ""
    if sys.platform.startswith("win"):
        return WINDOWS
    return shutil.which("xdg-open") or ""


def _result(opened: bool, how: str = "", detail: str = "") -> dict:
    return {
        "error": None,
        "content": {"opened": bool(opened), "how": how, "detail": detail},
    }


def open_report(path: str) -> dict:
    """``{"error", "content": {"opened", "how", "detail"}}``. Never raises."""
    try:
        if not settings.qa_mobile_run_enabled:
            return {"error": FLAG_REFUSAL, "content": None}
        target = str(path or "")
        if not target or not Path(target).is_file():
            return _result(False, "", "there is no report file at that path")
        opener = _opener()
        if not opener:
            return _result(False, "", NO_OPENER)
        if opener == WINDOWS:
            starter = getattr(os, "startfile", None)
            if starter is None:
                return _result(False, "", NO_OPENER)
            starter(target)
            return _result(True, "os.startfile")
        subprocess.Popen(
            [opener, target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return _result(True, opener)
    except Exception as exc:
        logger.warning("mobile.open_report: could not open %s: %s", path, exc)
        return _result(False, "", str(exc)[:200])
