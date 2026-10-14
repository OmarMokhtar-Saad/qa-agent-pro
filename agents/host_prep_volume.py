"""Volume floor: this prep's contract, the verdict on a suite, and its fact lines."""

from __future__ import annotations

from typing import NamedTuple

from agents.host_category_jobs import normalize_category

# Batch 1 (2026-08-09), thresholds revised after review round 1.
#
# _VOLUME_REFUSE_RATIO -- the share of the prep's OWN summed per-category floor
# below which a finalize is REFUSED rather than warned about.
# _VOLUME_WARN_SLACK is GONE (E02, 2026-08-20). It allowed ONE category to sit
# exactly one case under the floor before being reported -- measured against the
# two known-good runs of 2026-08-04 (suites 26600607... = 99 cases and
# 4ecf093a... = 97 cases, band-2, floor 12), which bottom out at EXACTLY 12 per
# category. It is not relaxed but SUBSUMED: with redistribution legitimate, a
# category is reported only when it is empty or below HALF its floor, and every
# 11/12 dip that the slack existed to silence is far inside that line. Leaving
# it in place would have been a second, tighter threshold that could never fire.
# An EMPTY category was never slack and still is not.
# _VOLUME_MAX_NAMED -- how many short categories the note lists by name. All
# 8 canonical categories fit, so the 08-09 shape (every category short)
# names them all; the cap only bounds a longer, hand-built list.
#
# The asymmetry survives the slack's deletion and is now the whole design: a
# CATEGORY may come in under its floor, the TOTAL may not. At floor 12, two
# categories at 11 (94 of 96) still warn -- not because 11 is too few for a
# category, but because 94 is too few for the suite, which is the number the
# prep actually contracted for and the one the 2026-08-09 collapse violated.
#
# Module CONSTANTS, not settings fields: every decision here keys off the prep's
# stamps so a mid-flow .env flip cannot change an in-flight prep, and a ratio
# that lives in code can only move with a code update -- which
# version_skew_note already discloses to the tester.

_VOLUME_REFUSE_RATIO = 0.5
_VOLUME_MAX_NAMED = 8


class RoutePhrases(NamedTuple):
    """The finalize-route wording the handler resolves on the live seam."""

    short: str
    long: str


class VolumeQuery(NamedTuple):
    """The caller-side inputs of one volume floor check (phrases are resolved later)."""

    meta: object
    cases: list
    prep_id: str
    ack: bool = False
    post_dedup: bool = False
    staged: int = 0


class VolumeCheck(NamedTuple):
    """The inputs of one volume_floor_note call."""

    meta: object
    cases: list
    prep_id: str
    ack: bool
    post_dedup: bool
    staged: int
    phrases: RoutePhrases


class VolumeVerdict(NamedTuple):
    """The decision behind volume_floor_note, with no wording in it."""

    mode: str
    floor: int
    names: list
    total: int
    unknown: int
    short: list
    empty: list
    refused_before: bool

    @property
    def floor_total(self) -> int:
        return self.floor * len(self.names)


class _Tally(NamedTuple):
    total: int
    unknown: int
    floor: int
    floor_total: int
    empty: bool


def volume_contract(meta: object) -> "tuple[int, list] | None":
    """This prep's ``(floor, category names)`` stamps, or None when it has none."""
    if not isinstance(meta, dict) or not meta.get("volume_floor"):
        return None
    try:
        floor = int(meta.get("volume_min_cases") or 0)
    except (TypeError, ValueError, OverflowError):
        floor = 0
    names = [
        str(n).strip() for n in (meta.get("volume_categories") or []) if str(n).strip()
    ]
    if floor <= 0 or not names:
        return None
    return floor, names


def bucket_cases_by_category(cases: list) -> "tuple[dict, int, int]":
    """``(count per canonical category, unlabelled count, total)`` of ``cases``."""
    counts: dict = {}
    unknown = 0
    total = 0
    for tc in cases or []:
        total += 1
        canon = normalize_category(getattr(tc, "category", None))
        if canon:
            counts[canon] = counts.get(canon, 0) + 1
        else:
            unknown += 1
    return counts, unknown, total


def _shortfalls(names: list, counts: dict, floor: int) -> "tuple[list, list, list]":
    """``(short, empty, collapsed)`` category lists."""
    short = [(n, counts.get(n, 0)) for n in names if counts.get(n, 0) < floor]
    empty = [n for n, got in short if got == 0]
    # E02: below HALF its floor a category has not judged its material thin,
    # it has collapsed -- that is the 08-09 shape surviving inside one
    # category. Everything between that line and the floor is the
    # redistribution the prompt now asks for and is not reported.
    collapsed = [n for n, got in short if got < floor * _VOLUME_REFUSE_RATIO]
    return short, empty, collapsed


def _base_mode(stamps: dict, t: _Tally) -> str:
    # A materially-short TOTAL refuses outright. A per-category refusal is
    # restricted to a genuinely ABSENT category on a prep that asked for the
    # fan-out, and only when the unlabelled cases are too few to account for
    # it -- everything else is a warning.
    if t.total < t.floor_total * _VOLUME_REFUSE_RATIO:
        return "refuse"
    if bool(stamps.get("parallel_fanout")) and t.empty and t.unknown < t.floor:
        return "refuse"
    return "warn"


def _final_mode(check: VolumeCheck, stamps: dict, mode: str) -> str:
    if check.post_dedup:
        # F5 (2026-08-15): warn, never stay silent -- the suite is already
        # final, and the tester must not lose it over cases the server removed.
        return "warn"
    if mode == "refuse" and check.ack and bool(stamps.get("volume_refused")):
        return "acked"
    return mode


def _tally(check: VolumeCheck, floor: int, names: list) -> "tuple[_Tally, list, list]":
    """The case counts plus the ``short`` list and the ``collapsed`` list."""
    counts, unknown, total = bucket_cases_by_category(check.cases)
    short, empty, collapsed = _shortfalls(names, counts, floor)
    tally = _Tally(total, unknown, floor, floor * len(names), bool(empty))
    return tally, short, collapsed


# Is this suite too thin for THIS prep's own floor? None means silent. Pure:
# reads the prep's stamps and the cases, renders nothing. The why of every
# rule is on volume_floor_note.
def volume_verdict(check: VolumeCheck) -> "VolumeVerdict | None":
    """The verdict for ``check``, or None when the suite honours the floor."""
    contract = volume_contract(check.meta)
    if contract is None:
        return None
    floor, names = contract
    stamps: dict = check.meta  # type: ignore[assignment]  # a dict once it has a contract
    t, short, collapsed = _tally(check, floor, names)
    # Clean exit: the summed floor is met AND no category is empty or
    # collapsed. Both are checked because unlabelled cases belong to no
    # category; once either opens the note, it names every short category.
    if t.total >= t.floor_total and not collapsed and not t.empty:
        return None
    mode = _final_mode(check, stamps, _base_mode(stamps, t))
    empty = [n for n, got in short if got == 0]
    refused_before = bool(stamps.get("volume_refused"))
    return VolumeVerdict(
        mode, floor, names, t.total, t.unknown, short, empty, refused_before
    )
