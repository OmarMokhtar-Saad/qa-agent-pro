"""Attribute every app turn to exactly one case window, or say why not."""

from __future__ import annotations

from tools.mobile_evidence.evidence_join_model import Join, JoinMeta, Tallies
from tools.mobile_evidence.evidence_notes import (
    NO_CLOCK_NOTE,
    NO_TURN_IN_ANY_CASE_NOTE,
    ONE_TURN_NOTE,
)
from tools.mobile_evidence.evidence_place import _place_turn, _PlaceCtx
from tools.mobile_evidence.evidence_windows import case_windows


def _join_note_bits(timed, ambiguous, unclocked):
    """The caveats a join carries, one string each."""
    note_bits = []
    if any(not w["exact"] for w in timed):
        note_bits.append(NO_CLOCK_NOTE)
    if ambiguous:
        note_bits.append(
            "%d turn(s) fell between two cases and are shown on neither card: %s"
            % (len(ambiguous), ", ".join(str(t) for t, _n in ambiguous))
        )
    if unclocked:
        note_bits.append(
            "%d turn(s) carry no clock and could not be placed" % len(unclocked)
        )
    return note_bits


def _said_by_turn(report):
    """``turnId`` -> what the app heard on it; the last utterance of a turn wins."""
    said = {}
    for u in report.get("utterances") or []:
        if u.get("turnId"):
            said[u["turnId"]] = (u.get("text") or "").strip()
    return said


def _synthetic_join(timed):
    """The join of a profile with no turn-start marker: one synthetic turn per case."""
    pairs = [(w["tc_id"], "case/" + w["tc_id"]) for w in timed if w["tc_id"]]
    note = ONE_TURN_NOTE if pairs else "no case window and no app turn: nothing to join"
    return Join(pairs, Tallies(), JoinMeta(True, note))


def _empty_acc():
    return {
        "pairs": [],
        "ambiguous": [],
        "outside": [],
        "unclocked": [],
        "confirmed": 0,
        "unconfirmed": 0,
        "mismatched": 0,
        "claimed": set(),
    }


def join(report, cases, windows=None):
    """Attribute every app turn to exactly one case window, or say why not."""
    windows = windows if windows is not None else case_windows(cases)
    timed = [w for w in windows if w.get("lo") is not None]
    by_tc = {str(c.get("tc_id") or ""): c for c in (cases or []) if isinstance(c, dict)}
    said = _said_by_turn(report)
    turns = list(report.get("turns") or [])
    if not turns:
        # D2: no turn-start marker -> one synthetic turn per case.
        return _synthetic_join(timed)
    ctx, acc = _PlaceCtx(report, timed, by_tc, said), _empty_acc()
    for t in turns:
        _place_turn(ctx, t, acc)
    return _join_from_placed(timed, acc)


def _tallies(timed, acc):
    """The ``Tallies`` of the join: ``ambiguous`` .. ``mismatched``, in field order."""
    empty = [
        w["tc_id"] for w in timed if w["tc_id"] and w["tc_id"] not in acc["claimed"]
    ]
    return Tallies(
        acc["ambiguous"],
        acc["outside"],
        acc["unclocked"],
        empty,
        acc["confirmed"],
        acc["unconfirmed"],
        acc["mismatched"],
    )


def _join_from_placed(timed, acc):
    """The Join for a run with turns, from what ``_place_turn`` accumulated."""
    pairs, tallies = acc["pairs"], _tallies(timed, acc)
    note_bits = _join_note_bits(timed, acc["ambiguous"], acc["unclocked"])
    if not pairs and timed:
        # NOT ONE TURN LANDED. Whatever turns the file holds describe other
        # sessions of the app, so keeping them costs every case its evidence and
        # gains nothing. The synthetic fallback attributes by the clock instead,
        # which is the only thing these records and these cases share.
        note_bits.append(NO_TURN_IN_ANY_CASE_NOTE)
        pairs = [(w["tc_id"], "case/" + w["tc_id"]) for w in timed if w["tc_id"]]
        return Join(pairs, tallies, JoinMeta(True, "; ".join(note_bits)))
    return Join(pairs, tallies, JoinMeta(False, "; ".join(note_bits)))
