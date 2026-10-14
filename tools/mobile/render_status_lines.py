"""Row builders for the ``qa_mobile_status`` block.

Twelfth layer of the ``render_*`` split of ``render.py``.

``_final_lines`` -- the recorded verdict of an ended run as a one-row list, or
``[]``.

``_status_head_lines`` -- the identity rows of a status block, capture and
final verdict included.

``_status_stopped_lines`` -- the restart instruction for a stopped explore run,
or ``[]``.

LEADS the block, on purpose, and that ordering is the whole point: a forward
action below the fold is one a model does not act on. Observed -- a model that
met a stopped run and got only a status dropped to 41 raw `adb` shell calls,
bypassing the destructive guard, the IME, evidence capture and the step record,
and then presented an earlier run's report as the report of that work.

IT ASSERTS EXACTLY WHAT THE PRODUCER ESTABLISHED and nothing wider.
`explore_runner.stop_reason` -- reaching here as `explore_stop` via
`session.resolve` -- establishes THAT the run stopped and names it in its own
words, so the restart instruction is sound and the reason is quoted rather than
interpreted. A previous version added a cause ("its budget is spent"), which an
executing review found false for a `goal_reached` run stopped at turn 4 of 30.
There is deliberately no per-reason prose and no table to keep in step with the
producer.

``_status_abandoned_lines`` -- the pick-it-back-up instruction for an
abandoned run, or ``[]``.

Deliberately NOT the takeover wording: no other chat holds this run, so "taken
over" would send a tester looking for a session that does not exist. What they
need is the one call that picks it back up, spelled out.
"""

from __future__ import annotations

from tools.mobile.render_capture import capture_line
from tools.mobile.render_field import _field


def _final_lines(final: object) -> list[str]:
    """The recorded verdict of an ended run as a one-row list, or ``[]``."""
    if not isinstance(final, dict) or not final:
        return []
    reason = _field(final.get("reason"))
    stop = _field(final.get("stop"))
    return [
        "- result: **"
        + _field(final.get("outcome"), "unknown")
        + "**"
        + (" — " + reason if reason else "")
        + (" (`" + stop + "`)" if stop else "")
    ]


def _status_head_lines(body: dict) -> list[str]:
    """The identity rows of a status block, capture and final verdict included."""
    lines = [
        "## Mobile run `" + _field(body.get("run_id"), "?") + "`",
        "",
        # 'unknown', not '?': a question mark here reads as a rendering
        # artifact, and a tester cannot tell an odd state from a state the
        # server could not read. The two lines below already spell theirs
        # out, so this keeps the block consistent.
        "- state: **" + _field(body.get("state"), "unknown") + "**",
        "- lane: " + _field(body.get("lane"), "unknown"),
        "- app: `" + _field(body.get("package"), "(none)") + "`",
        "- device: " + _field(body.get("serial"), "(not attached)"),
    ]
    capture = body.get("capture")
    if isinstance(capture, dict) and capture:
        lines.append(capture_line(capture))
    lines.extend(_final_lines(body.get("final")))
    return lines


def _status_stopped_lines(body: dict) -> list[str]:
    """The restart instruction for a stopped explore run, or ``[]``."""
    stopped = _field(body.get("explore_stop"))
    if not stopped:
        return []
    return [
        "**This run has stopped — `"
        + stopped
        + "`.** It cannot be continued: call `qa_mobile_test` to start "
        "a NEW run.",
        "",
    ]


def _status_abandoned_lines(body: dict) -> list[str]:
    """The pick-it-back-up instruction for an abandoned run, or ``[]``."""
    if str(body.get("state") or "") != "abandoned":
        return []
    return [
        "",
        "**Nothing has driven this run for "
        + str(int(float(body.get("lease_age") or 0)))
        + "s, so it looks abandoned.** Its finished cases are safe on "
        "disk. To pick it up, call `qa_mobile_test` with "
        '`run_id="' + _field(body.get("run_id"), "?") + '"` and '
        "`continue_run=true`.",
    ]
