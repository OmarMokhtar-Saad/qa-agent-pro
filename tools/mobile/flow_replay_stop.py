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
    if outcome == "screen_mismatch" and detail:
        return detail
    base = "the step did not run as planned"
    return "%s (%s)" % (base, outcome) if outcome else base


def cut_why(ctx: Any, screen: Any, step: dict) -> str:
    """The stop reason when *screen* is a dialog or another app, else ``""``."""
    view = CutView(screen, str(step.get("op") or ""))
    cut: Cut = _cut_of(ctx, view)
    if not cut.kind:
        return ""
    return "%s (%s): %s" % (LEFT_APP, cut.kind, _cut_detail(cut, ctx, {}))


#: Ops whose step may do nothing: an optional tap, or a wait for the screen to settle.
OPTIONAL_OPS = frozenset(
    {"tap_text_if_present", "wait_until_gone", "wait_until_idle", "wait_until_changed"}
)
#: The note line when a chained flow is not started because it has nothing left to do.
CHAIN_DONE = (
    "chain stopped: the rest of flow `%s` (from step %d) only taps optional "
    "prompts or waits on the screen already reached."
)


def _inert(template: dict, at: int) -> bool:
    meta = template.get("meta") or {}
    before, after = meta.get("before") or [], meta.get("after") or []
    key = before[at] if at < len(before) else ""
    last = after[at] if at < len(after) else ""
    return not last or last == key


def inert_tail(template: dict, pos: int) -> bool:
    """True when every step from *pos* is optional and leaves the screen as it was."""
    steps = (template.get("steps") or [])[pos:]
    if not steps:
        return False
    return all(
        s.get("op") in OPTIONAL_OPS and _inert(template, pos + i)
        for i, s in enumerate(steps)
    )


def chain_done(body: dict, name: str, pos: int) -> None:
    """Add the stop reason to the replay note the model reads."""
    last = body.get("auto_replay")
    if isinstance(last, dict):
        why = CHAIN_DONE % (name, pos + 1)
        last["note"] = "%s\n%s" % (last.get("note") or "", why)
