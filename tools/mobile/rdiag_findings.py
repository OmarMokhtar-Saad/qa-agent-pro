"""Diagnostics findings section: what an exploratory run found, one line per turn."""

from __future__ import annotations

from tools.mobile import charter as charter_mod
from tools.mobile.report_text import _text, esc
from tools.mobile.report_verdict import explore_stop
from tools.mobile.report_widgets import _Head, _sechead


def _replayed_turns(explore: dict, turns: int) -> int:
    try:
        replayed = max(0, int(turns or 0))
    except (TypeError, ValueError, OverflowError):
        replayed = 0
    # The CARD count under-counts turns: a turn whose script failed to parse
    # returns before it is checkpointed (``session._submit_explore``), while
    # ``explore_runner.next_turn`` had already incremented and persisted the
    # turn number -- so mrun-20260907-174354-758700 replayed 19 turns, wrote 17
    # cards, and this lede said "17 turns" with no silent turns at all.
    # The manifest's own counter is taken when it is LARGER; a smaller value
    # (an older build that never wrote one, a manifest not yet persisted) would
    # under-count in the other direction, and the lede must never claim fewer
    # turns than there are cards on the page.
    try:
        recorded = max(0, int(explore.get("turn") or 0))
    except (TypeError, ValueError, OverflowError):
        recorded = 0
    return max(replayed, recorded)


def _spoken_turns(notes: list) -> set:
    # Counted by DISTINCT turn, never by row. A turn resubmitted with a finding
    # appends a second row while leaving ONE checkpoint, so a row count read
    # "2 findings over 1 turn" and drove the silent count negative into its own
    # clamp -- which hid the miscount instead of reporting it.
    spoke = set()
    for note in notes:
        try:
            spoke.add(int(note.get("turn") or 0))
        except (TypeError, ValueError, OverflowError):
            continue
    return spoke


def _silent_clause(spoken: int, replayed: int) -> str:
    silent = max(0, replayed - spoken)
    return (
        ", and "
        + str(silent)
        + " turn"
        + ("" if silent == 1 else "s")
        + " recorded none — every turn is asked for one, so those are "
        "gaps in the evidence rather than turns with nothing to report"
        if silent
        else ""
    )


def _stop_clause(stop: str) -> str:
    return (
        "This session ended: " + esc(stop, 40) + "."
        if stop
        else "This session has NOT ended, so this list is incomplete."
    )


def _findings_lede(explore: dict, turns: tuple, tail: tuple) -> str:
    spoken, replayed = turns
    stop, terms = tail
    return (
        "Goal: "
        + (esc(explore.get("goal"), 400) or "(not recorded)")
        + ". "
        + str(spoken)
        + " finding"
        + ("" if spoken == 1 else "s")
        + " recorded over "
        + str(replayed)
        + " turn"
        + ("" if replayed == 1 else "s")
        + _silent_clause(spoken, replayed)
        + ". "
        + _stop_clause(stop)
        + ((" " + esc(terms, 800)) if terms else "")
    )


def _explore_notes(body: dict) -> tuple | None:
    if str(body.get("lane") or "") != "explore":
        return None
    explore = body.get("explore")
    explore = explore if isinstance(explore, dict) else {}
    notes = [
        note for note in list(explore.get("findings") or []) if isinstance(note, dict)
    ]
    return explore, notes


# What an exploratory run FOUND (the empty string for any other lane).
# What an exploratory run FOUND. ``""`` for any other lane.
#
# ``explore_runner.apply_turn_result`` has always accumulated
# ``manifest["explore"]["findings"]`` and NOTHING read it -- not this module,
# not the shell -- so a 20-minute session's entire output was invisible.
#
# Two honesty rules this section keeps, both of which the page already
# applies elsewhere:
#
# * an explore run that ``explore_runner.stop_reason`` does not call stopped
# is PARTIAL (``_is_partial``), so the reader is told the list is incomplete
# rather than shown a conclusion. NOT ``explore.stop`` alone, which is one
# of that producer's three clauses: a run whose deadline passed with no
# final turn has stopped, and reading the field said otherwise forever;
# * a turn that recorded NO finding is counted out loud. The packet asks for
# one every turn, so silence is a gap in the evidence rather than a turn
# with nothing to report, and a reader who is not told cannot tell the two
# apart.
#
# Every interpolated value goes through :func:`esc`, and the table carries no
# ``id`` and no ``data-sort`` -- both are wired to the shell's own script.
#
# The run's TERMS, including every default the tester did not set: a
# silent default is a run whose terms nobody can reconstruct afterwards.
# `charter.describe_terms` is the ONE producer of this sentence, and it
# returns "" for a run with no charter -- every run recorded before step 4
# -- so those pages render exactly as they did.
def _findings_section(manifest: dict, turns: int) -> str:
    body = manifest if isinstance(manifest, dict) else {}
    found = _explore_notes(body)
    if found is None:
        return ""
    explore, notes = found
    replayed = _replayed_turns(explore, turns)
    spoke = _spoken_turns(notes)
    stop = _text(explore_stop(body), 40)
    terms = charter_mod.describe_terms(explore.get("charter"))
    lede = _findings_lede(explore, (len(spoke), replayed), (stop, terms))
    return _sechead(
        "findings",
        _Head("What exploring found", "one line per turn", lede, 3),
        _findings_table(notes),
    )


def _findings_table(notes: list) -> str:
    """The one-row-per-turn findings table, or the empty note."""
    rows = "".join(
        '<tr><td class="num">'
        + esc(note.get("turn"), 8)
        + '</td><td dir="auto">'
        + esc(note.get("note"), 600)
        + "</td></tr>"
        for note in notes
    )
    return (
        '<div class="tablewrap"><table class="cov"><thead><tr>'
        '<th scope="col" class="num">Turn</th>'
        '<th scope="col">What the turn reported</th>'
        "</tr></thead><tbody>" + rows + "</tbody></table></div>"
        if rows
        else '<p class="empty-note big">No turn recorded a finding.</p>'
    )
