"""App Tester UI: an installer screen with no known button is a wait, not a stop.

The package installer shows transient screens ("Staging app...", "Installing")
whatever their wording or locale. Any installer screen no handler acts on is
waited out, one screen read per loop turn; the flow stops only when the same
screen stays unchanged for ``QUIET_POLLS`` reads in a row.
"""

from __future__ import annotations

from typing import Any

from tools.mobile.dialog_install import INSTALLER_PACKAGES
from tools.mobile.install_sources.app_tester_ui_consts import QUIET_POLLS, _Run


def reset_hold(run: _Run) -> None:
    """Forget the held installer screen: something else was in front or acted."""
    run.held, run.held_polls = (), 0


def installer_waits(run: _Run, front: str, texts: Any) -> bool:
    """True while an installer screen keeps changing (or has just appeared)."""
    if front not in INSTALLER_PACKAGES:
        reset_hold(run)
        return False
    key = tuple(sorted(texts))
    if key != run.held:
        run.held, run.held_polls = key, 0
        return True
    run.held_polls += 1
    return run.held_polls < QUIET_POLLS
