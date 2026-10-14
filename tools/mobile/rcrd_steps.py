"""The step list and the technical-detail fold of a case card."""

from __future__ import annotations

from tools.mobile.report_frames import _Caption, _look_pic, _seq_frames
from tools.mobile.report_trace import _AFTER_LOOK, _step_html, _StepSlot
from tools.mobile.report_verdict import _app_logged
from tools.mobile.report_widgets import _PageData, _sec_block, _SecBlock
from tools.mobile_evidence import exchanges as ev_exchanges
from tools.mobile_evidence import render as ev_render


def _last_substitution(facts: dict) -> tuple:
    """The last look this case recorded, with its caption, or ``()`` when none was substituted."""
    return (
        (facts["last_look"], "the last look this case recorded at that screen")
        if facts.get("last_substituted") and facts.get("last_look")
        else ()
    )


# THE SAME STEP LIST AS A JOURNEY TURN. Each step shows the screen before
# and after it, resolved through `_look_pic`, so the ambiguous-look and
# not-captured notes stand in place of a picture exactly as they do there.
# The phone pair, the element maps and the lane-only sequence repeated those
# same screens two and three times per card.
#
# THE END STATE KEEPS ITS SUBSTITUTION. When the last action's own after
# look was not captured again, `_case_facts` names the last look this case
# recorded at that screen, and the last step's After shows it -- with the
# caption saying so, because the right picture under a caption claiming it
# is the end state would be the same defect in better clothes.
def _card_steps(facts: dict, screens: object, app: str) -> str:
    """The case's step list, or the honest note that none was replayed."""
    rows = facts["rows"]
    page = _PageData(screens, app)
    if not rows:
        return _card_steps_none(facts, page)
    last = _last_substitution(facts)
    return (
        '<ol class="steplist">'
        + "".join(
            _step_html(
                _StepSlot(facts["slug"], i),
                {**row, _AFTER_LOOK: last} if last and i == len(rows) - 1 else row,
                page,
            )
            for i, row in enumerate(rows)
        )
        + "</ol>"
    )


def _card_steps_none(facts: dict, page: _PageData) -> str:
    """The note for a case with no replayed step, with its first screen when known."""
    first_key = facts["first_obs"] or facts["first"]
    pic = _look_pic(first_key, page, _Caption("On screen")) if first_key else ""
    return (
        '<p class="empty-note big">No step has been replayed for this case yet.</p>'
        + (('<div class="steppics">' + pic + "</div>") if first_key else "")
    )


def _card_sequence(facts: dict, screens: object, loaded: dict | None) -> str:
    """The app's records and the lane's actions on one clock, or ``""`` without evidence."""
    rows = facts["rows"]
    if not loaded:
        return ""
    return ev_exchanges.seqlist(
        ev_render.sequence_items(
            loaded,
            facts["tc_id"],
            ev_render.Trace(rows, _seq_frames(rows, screens)),
        )
    )


def _card_run_sequence(facts: dict, screens: object, loaded: dict | None) -> str:
    """The folded "Run sequence" section, or ``""`` when there is nothing to merge."""
    rows = facts["rows"]
    merged = _card_sequence(facts, screens, loaded)
    note = "the app's records and the lane's actions on one clock"
    block = _SecBlock("Run sequence", merged, len(rows) if rows else None, note)
    return _sec_block(block) if merged else ""


# What the app heard and answered inside this case's window (plan P3), the
# wire, and this case's own API capture -- folded, as in a Journey turn.
# The network and capture blocks are NOT gated on `loaded`: the case runner
# writes both onto the checkpoint, so a run with no app profile has them.
def _card_tech(facts: dict, screens: object, loaded: dict | None) -> str:
    """The body of the card's folded "Technical detail" block."""
    return (
        (ev_render.turns_table(loaded, facts["tc_id"]) if _app_logged(loaded) else "")
        + ev_render.case_network(facts.get("network"))
        + ev_render.case_capture(facts.get("capture"))
        + _card_run_sequence(facts, screens, loaded)
    )
