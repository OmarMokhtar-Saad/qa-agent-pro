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
from pathlib import Path
from typing import Any

from config.settings_guard import never_raise
from tools.mobile import platform_info

logger = logging.getLogger(__name__)

NO_OPENER = "no file opener on this machine"

WINDOWS = "startfile"

# Fire-and-forget: all three streams on DEVNULL and the child in its own session.
_DETACHED: dict[str, Any] = {
    "stdin": subprocess.DEVNULL,
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
    "start_new_session": True,
}


def _opener() -> str:
    """The opener for this platform, or ``""``.

    Private, so the kill-switch invariant test skips it -- and correctly: it
    resolves a path and performs no effect.
    """
    # `platform_info.is_windows()` rather than this module's old
    # `sys.platform.startswith("win")`. Both are true on every real Windows
    # host, so this is not a behaviour change -- it deletes a SECOND definition
    # of Windows, and it sat in the one module whose Windows branch
    # (`os.startfile`) has never actually executed.
    if platform_info.is_macos():
        return shutil.which("open") or ""
    if platform_info.is_windows():
        return WINDOWS
    return shutil.which("xdg-open") or ""


def _result(opened: bool, how: str = "", detail: str = "") -> dict:
    return {
        "error": None,
        "content": {"opened": bool(opened), "how": how, "detail": detail},
    }


def _target(path: str) -> str:
    """The report file to open (a folder resolves to its index), or ``""``."""
    target = str(path or "")
    # The report is a FOLDER now, so a caller may hand either the folder or
    # its index. One resolution, here, rather than one in every caller.
    if target and Path(target).is_dir():
        target = str(Path(target) / "index.html")
    return target if target and Path(target).is_file() else ""


def _startfile(target: str) -> dict:
    """The Windows branch: ``os.startfile`` where this platform has it."""
    starter = getattr(os, "startfile", None)
    if starter is None:
        return _result(False, "", NO_OPENER)
    starter(target)
    return _result(True, "os.startfile")


def open_report(path: str) -> dict:
    """``{"error", "content": {"opened", "how", "detail"}}``. Never raises."""
    with never_raise(
        logger,
        "mobile.open_report: could not open %s: %s",
        path,
        level=logging.WARNING,
        with_exc=True,
    ) as g:
        target = _target(path)
        if not target:
            return _result(False, "", "there is no report file at that path")
        opener = _opener()
        if not opener:
            return _result(False, "", NO_OPENER)
        if opener == WINDOWS:
            return _startfile(target)
        subprocess.Popen([opener, target], **_DETACHED)
        return _result(True, opener)
    return _result(False, "", str(g.exc)[:200])
