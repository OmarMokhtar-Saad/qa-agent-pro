"""The one-line case verdict and the soft "continue?" gate.

Eleventh layer of the ``render_*`` split of ``render.py``.

``verdict_line`` -- ONE line per case. A run of 200 cases is 200 lines, not 200
sections.

``screen`` (the pruned screen the step ended on) and ``previous_id`` (the
screen id before it) add a bounded screen summary after the tally.

A case the SERVER failed because the app died carries its disclosure here --
both chat surfaces reach this function (the submit reply directly, and
``status_block``'s rows through it), so the crash is stated once and shown
twice.

``gate_block`` -- the soft "continue?" gate. Asks the TESTER, never assumes.
"""

from __future__ import annotations

from tools.mobile import case_label
from tools.mobile.render_crash import crash_note
from tools.mobile.render_field import _field
from tools.mobile.render_status_word import status_word
from tools.mobile.render_summary import screen_block
from tools.mobile.render_tally import _typed_field_tally

_MARKS = {
    "pass": "✅",
    "fail": "❌",
    "blocked": "⛔",
    "unverified": "⚠️",
    "needs_tester": "❓",
    "needs_model": "\U0001f501",
}


def _verdict_tail(case: object, screen: object, previous_id: str) -> str:
    note = crash_note(case)
    tally = _typed_field_tally([case if isinstance(case, dict) else {}])
    block = screen_block(screen, previous_id)
    return (
        (("\n\n" + note) if note else "")
        + (("\n\n" + tally.strip()) if tally else "")
        + (("\n\n" + block) if block else "")
    )


def _verdict_head(body: dict) -> str:
    # Every field here arrives in the model's submitted step and is replayed
    # from disk in a later chat -- the same channel as the status block, and
    # `tc_id` sits in the same single backticks that a planted fence escaped.
    verdict = _field(status_word(body))
    reason = _field(body.get("reason"))[:160]
    return (
        _MARKS.get(verdict, "•")
        + " `"
        + case_label.label_for({"tc_id": _field(body.get("tc_id"))})
        + "` "
        + _field(body.get("title"))[:80]
        + " — **"
        + (verdict or "unknown")
        + "**"
        + ((" — " + reason) if reason else "")
    )


def verdict_line(case: object, screen: object = None, previous_id: str = "") -> str:
    """ONE line per case, with its crash note, tally and screen summary."""
    body = case if isinstance(case, dict) else {}
    tail = _verdict_tail(case, screen, previous_id)
    return _verdict_head(body) + tail


def gate_block(point: object) -> str:
    """The soft "continue?" gate. Asks the TESTER, never assumes."""
    body = point if isinstance(point, dict) else {}
    return (
        "## "
        + str(int(body.get("done") or 0))
        + " of "
        + str(int(body.get("total") or 0))
        + " cases done — keep going?\n\n"
        + str(len(body.get("failed") or []))
        + " failed so far. Ask the tester whether to continue, and only if they "
        "say yes call `qa_mobile_test` again with the same `run_id` and "
        "`continue_run=true`. Stopping here loses nothing: every case done is "
        "already checkpointed and the run resumes from this point in any chat."
    )
