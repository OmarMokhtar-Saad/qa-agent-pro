"""The shape of a join: which turn belongs to which case, and what had to be true."""

from __future__ import annotations

from typing import NamedTuple


def _by_case(pairs) -> dict:
    """``tc_id`` -> its turn ids, in pair order."""
    out: dict = {}
    for tc_id, tid in pairs:
        out.setdefault(tc_id, []).append(tid)
    return out


class Tallies(NamedTuple):
    """What the join found besides the pairs, in the order ``Join`` reads them."""

    ambiguous: object = ()
    outside: object = ()
    unclocked: object = ()
    empty_cases: object = ()
    confirmed: int = 0
    unconfirmed: int = 0
    mismatched: int = 0


class JoinMeta(NamedTuple):
    """How the turns were made: one-per-case (synthetic), and a note on it."""

    synthetic: bool = False
    note: str = ""


class Join:
    """The mapping, and everything that had to be true for it to be one.

    ``pairs``       [(tc_id, turnId)]
    ``ambiguous``   [(turnId, note)] -- between two cases, on neither card
    ``outside``     [(turnId, why)]  -- before the first or after the last case
    ``unclocked``   [turnId]         -- the capture holds no clock for the turn
    ``empty_cases`` [tc_id]          -- cases whose window holds no turn
    ``confirmed`` / ``unconfirmed`` / ``mismatched`` -- turn-text confirmation counts
    ``synthetic``   True when turns were made one-per-case (no turn-start marker)
    """

    __slots__ = (
        "pairs",
        "ambiguous",
        "outside",
        "unclocked",
        "empty_cases",
        "confirmed",
        "unconfirmed",
        "mismatched",
        "synthetic",
        "note",
        "by_case",
    )

    def __init__(self, pairs, tallies=Tallies(), meta=JoinMeta()):
        self.pairs, self.ambiguous = list(pairs), list(tallies.ambiguous)
        self.outside, self.unclocked = list(tallies.outside), list(tallies.unclocked)
        self.empty_cases = list(tallies.empty_cases)
        self.confirmed, self.unconfirmed = tallies.confirmed, tallies.unconfirmed
        self.mismatched = tallies.mismatched
        self.synthetic, self.note = meta.synthetic, meta.note
        self.by_case = _by_case(self.pairs)

    @property
    def ok(self):
        return not self.ambiguous and not self.mismatched

    def turn_ids(self, tc_id):
        return list(self.by_case.get(str(tc_id), []))
