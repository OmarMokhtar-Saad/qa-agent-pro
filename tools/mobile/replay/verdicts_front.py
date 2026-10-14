"""Replay engine: the brought-to-front launch verdict. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile.replay.screen import _screen_package
from tools.mobile.replay.verdicts_consts import BROUGHT_TO_FRONT


class Landing(NamedTuple):
    """A launch's outcome, the screen in hand, the context and the package it named."""

    outcome: Any
    screen: Any
    ctx: Any
    named: Any = None


# True when a `launch` only moved THIS app's task to the front of a screen
# that was already this app, so the screen in hand is still current.
#
# The package check is what makes the skip safe: `am start` says the same
# words when the app was in the BACKGROUND, and then the screen in hand is
# the launcher and must be re-read. A launch that NAMED another package
# (an App Tester, say) never counts: the screen in hand is the target's,
# the app now in front is not, so the redump must run.
def _brought_to_front(landing: Landing) -> bool:
    """True when a `launch` only moved THIS app's task to the front."""
    outcome, screen, ctx = landing.outcome, landing.screen, landing.ctx
    if landing.named and str(landing.named) != str(getattr(ctx, "package", "") or ""):
        return False
    body = outcome.get("content") if isinstance(outcome, dict) else None
    if not isinstance(body, dict):
        return False
    said = str(body.get("out") or "") + str(body.get("err") or "")
    package = str(getattr(ctx, "package", "") or "")
    return (
        bool(package)
        and BROUGHT_TO_FRONT in said
        and _screen_package(screen) == package
    )
