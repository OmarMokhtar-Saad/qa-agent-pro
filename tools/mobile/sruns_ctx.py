"""Mobile session: the per-call device context and the coverage sentence (cut from session_runs.py, C5 w17)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile import run_store
from tools.mobile.replay.context import Context
from tools.mobile.session_core import LANE_EXPLORE
from tools.mobile.session_records import permissions_record

logger = logging.getLogger("tools.mobile.session")


def _handed_home(body: dict) -> tuple[str, str]:
    """``(home package, the app under test a multi-app goal handed over)``."""
    explore = body.get("explore")
    explore = explore if isinstance(explore, dict) else {}
    return str(body.get("package") or ""), str(explore.get("app") or "")


# THE APP UNDER TEST, which a multi-app goal may have handed over (fix
# round 3, item 1): `explore["app"]` is written only by
# `_submit_explore`, after `explore_runner.handover` checked it against
# the package the server itself saw in front. The run's own package stays
# `home_package`, the only one `clear_app_data` may wipe. The launch
# activity belongs to the home app, so after a hand-over it is left empty
# and resolved from the device instead.
#
# `asserts_expected`: the lane decides whether a script that asserted nothing
# is an omission worth naming. Set HERE, at the one production construction
# site, which both explore entry points (``next_packet`` and ``submit``)
# already go through -- so the value cannot reach the executor by one path
# and not the other.
#
# `run_id`/`lane`: per-app knowledge. ``begin_run`` reads the env and app
# version from the run's manifest.
def context_for(resolved: object) -> Context:
    """Build the per-call device context. Holds no tester value by default."""
    body = resolved if isinstance(resolved, dict) else {}
    home, handed = _handed_home(body)
    return Context(
        serial=str(body.get("serial") or ""),
        package=handed or home,
        home_package=home,
        activity="" if handed else str(body.get("activity") or ""),
        asserts_expected=str(body.get("lane") or "") != LANE_EXPLORE,
        run_id=str(body.get("run_id") or "") or None,
        lane=str(body.get("lane") or "") or None,
        permissions=permissions_record(body.get("permissions")),
    )


def _coverage_meta(body: dict) -> dict:
    """The run facts ``verdict_coverage`` reads."""
    return {
        "lane": body.get("lane"),
        "total": body.get("total"),
        "explore": body.get("explore"),
    }


# Thin on purpose. The producer is ``run_store.verdict_coverage`` /
# ``coverage_phrase`` -- the same pair ``report.py`` renders from -- and this
# is only the seam that lets the chat surface reach it without ``render.py``
# importing a store: that module imports nothing internal, and the two blocks
# of one status reply must print the SAME string rather than two counts that
# disagree about whether a status may stand in for a verdict.
def coverage_line(resolved: object, cases: object) -> str:
    """The ONE verdict-coverage sentence for a resolved run. Never raises."""
    with never_raise(
        logger,
        "mobile.session: could not summarise verdict coverage",
        level=logging.WARNING,
    ):
        body = resolved if isinstance(resolved, dict) else {}
        return run_store.coverage_phrase(
            run_store.verdict_coverage(cases, _coverage_meta(body))
        )
    return ""
