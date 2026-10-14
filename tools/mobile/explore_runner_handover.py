"""The exploratory lane's hand-over: which package a turn reply may hand the guard to.

Cut from ``tools.mobile.explore_runner``; the facade re-exports ``handover`` (identity).
Never imports the facade.
"""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile.explore_runner_consts import (
    _PACKAGE_RE,
    HANDOVER_REFUSED_PREFIXES,
    HANDOVER_REFUSED_WORDS,
    MAX_PACKAGE_CHARS,
)

logger = logging.getLogger("tools.mobile.explore_runner")


def _package_ok(wanted: str) -> bool:
    if not wanted or len(wanted) > MAX_PACKAGE_CHARS:
        return False
    if not _PACKAGE_RE.match(wanted):
        return False
    lowered = wanted.lower()
    if wanted == "android" or lowered.startswith(HANDOVER_REFUSED_PREFIXES):
        return False
    return not any(word in lowered for word in HANDOVER_REFUSED_WORDS)


# The package a turn reply hands the run over to, or ``""``. Never raises.
#
# Fix round 3, item 1. A multi-app goal -- open App Tester, update the app under test,
# open it, log in -- moves into a second app, and the guard kept comparing
# against the first: five turns on the right screen were refused as
# SCREEN_NOT_THE_APP. The reply's ``app`` is accepted only when ALL hold:
#
# * it is a well-formed package name no longer than
#   :data:`MAX_PACKAGE_CHARS`;
# * it is not a system surface: not ``android``, not under
#   :data:`HANDOVER_REFUSED_PREFIXES`, and containing none of
#   :data:`HANDOVER_REFUSED_WORDS`;
# * it EQUALS ``state["screen_package"]`` -- the package ``next_turn`` saw
#   in front when it built this turn's packet. The model can name the app
#   the flow has visibly moved into, and nothing else.
#
# ``""`` for anything else, including the app already handed over, so the
# caller writes only on a real change.
def handover(state: object, reply: object) -> str:
    """The package a turn reply hands the run over to, or ``""``. Never raises."""
    with never_raise(
        logger,
        "mobile.explore_runner.handover failed",
        level=logging.WARNING,
        exc_info=True,
    ):
        body = state if isinstance(state, dict) else {}
        said = reply if isinstance(reply, dict) else {}
        wanted = str(said.get("app") or "").strip()
        if not _package_ok(wanted):
            return ""
        if wanted != str(body.get("screen_package") or ""):
            return ""
        if wanted == str(body.get("app") or ""):
            return ""
        return wanted
    return ""
