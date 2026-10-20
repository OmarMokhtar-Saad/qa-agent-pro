"""One step of a case: its words, outcome, notes and the screens before and after."""

from __future__ import annotations

from dataclasses import dataclass

from tools.mobile.report_text import esc
from tools.mobile.report_widgets import _outcome_pill, _PageData
from tools.mobile.rtrc_plain import _parts_html, _step_words

#: A trace outcome in plain words. Anything unlisted reads as its own name.
OUTCOME_WORDS = {
    "ok": "done",
    "done": "finished",
    "assert_pass": "check passed",
    "assert_fail": "check failed",
    "visual_check": "visual check (judged by the model)",
    "left_app": "left the app",
    "permission_unclear": "permission pop-up unclear (host decides)",
    "route_mismatch": "not run (screen not as saved)",
    "screen_mismatch": "not run (screen not as saved)",
    "supplied": "tester answered",
    "knowledge_avoid": "stopped by a saved note",
    "-": "not run",
}


#: Row key the case card sets on its LAST step to ``(key, note)`` when it knows a
#: better After than the row's own key: the substituted end state, disclosed in
#: *note*. Carried on the row (a copy) so `_step_html` keeps five parameters.
_AFTER_LOOK = "_after_look"


@dataclass(frozen=True)
class _StepSlot:
    """Where one step sits in its case: the case slug and the step's position."""

    slug: str
    pos: int


def _step_facts(row: dict) -> tuple:
    """The outcome word and the (detail, notes applied) pair of a step."""
    outcome = str(row.get("outcome") or "-")
    word = OUTCOME_WORDS.get(outcome.lower(), outcome.replace("_", " "))
    detail = str(row.get("detail") or "")
    applied = str(row.get("notes_applied") or "")
    return word, (detail, applied)


def _step_text(row: dict, page: _PageData) -> str:
    """The step's plain words as markup."""
    return _parts_html(_step_words(row, page.library))


# One step: its words, its outcome and what the server said about it. NO
# picture: a goal shows ONE screenshot, its end state, and the run ONE
# recording -- per-step Before/After pairs buried both under dozens of frames.
def _step_html(slot: _StepSlot, row: dict, page: _PageData) -> str:
    """One step: its words, outcome and the server's notes."""
    word, (detail, applied) = _step_facts(row)
    pos = int(slot.pos)
    top = '<li class="step" id="step-' + slot.slug + "-" + str(pos) + '">'
    head = '<div class="stephead"><span class="stepn">' + str(pos + 1) + "</span>"
    act = '<p class="stepact" dir="auto">' + _step_text(row, page) + "</p>"
    notes = "".join(
        '<p class="stepnote" dir="auto">' + esc(line, 300) + "</p>"
        for line in (detail, applied, row.get("replayed"))
        if line
    )
    return top + head + act + _outcome_pill(word) + "</div>" + notes + "</li>"
