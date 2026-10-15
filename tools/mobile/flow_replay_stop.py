"""Why an auto-replay stops: a step that left the app, or landed somewhere unrecorded."""

from __future__ import annotations

from typing import Any

from tools.mobile.replay.settle_cut import Cut, CutView, _cut_detail, _cut_of

#: Start of the stop reason when the step took the foreground away from the app.
LEFT_APP = "the replay left the app under test"
#: The stop reason when the screen after a step is not one the flow recorded.
UNRECORDED = "the screen after it never matched any screen the flow recorded"


def _last(content: dict) -> dict:
    trace = content.get("trace") or []
    return trace[-1] if trace else {}


def failed_why(content: dict) -> str:
    """The stop reason for a step that did not run as planned, with its own detail."""
    last = _last(content)
    outcome = str(last.get("outcome") or "")
    detail = str(last.get("detail") or "")
    if outcome in ("left_app", "system_dialog"):
        return "%s (%s): %s" % (LEFT_APP, outcome, detail)
    base = "the step did not run as planned"
    return "%s (%s)" % (base, outcome) if outcome else base


def cut_why(ctx: Any, screen: Any, step: dict) -> str:
    """The stop reason when *screen* is a dialog or another app, else ``""``."""
    view = CutView(screen, str(step.get("op") or ""))
    cut: Cut = _cut_of(ctx, view)
    if not cut.kind:
        return ""
    return "%s (%s): %s" % (LEFT_APP, cut.kind, _cut_detail(cut, ctx, {}))
