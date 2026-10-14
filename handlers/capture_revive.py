"""Reviving shelved screens back into the tray, and the probes that decide whether to."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import capture_limits as _limits
from handlers.capture_evict import _normalised_ids, _note_eviction
from handlers.capture_shelf import _sweep_carry_shelf
from handlers.capture_tray_sweep import _sweep_capture_tray

logger = logging.getLogger("tools.mcp_handlers")


# DO NOT TAKE WHAT THIS TRAY CANNOT KEEP. A revived screen keeps
# its ORIGINAL created_at (deliberately, so it expires on its own
# capture clock) -- which makes it the OLDEST entry, and the
# oldest-first byte eviction in _sweep_capture_tray takes it
# first. Popping it off the shelf in the same breath removed it
# from BOTH pools. Measured 2026-09-06 with 8 shelved and 8 live
# 3 MiB screens against the 32 MiB cap: 8 revived, 2 peeked, 6
# gone permanently, while carry_forward_or_refuse reported the
# pre-eviction count of 8 to the tester and the missing six
# surfaced only as a generic 'unknown or expired id'.
#
# So the budget is checked BEFORE the shelf loses the item: what
# does not fit stays shelved and is NOT reported as revived. The
# return value is the list that actually resolves, which is what
#
# A NEW outcome with its own meaning -- the screen is safe, it
# just did not fit -- so it is recorded for the consumer that
# would otherwise call it unknown or expired. Every caller of
# this function reaches that consumer through peek_captures,
# including the one at the deferred-revive site that discards
# this return value (independent review, 2026-09-06).
def _fits_tray(cid: str, item: dict) -> bool:
    """True when *item* fits the tray's byte budget; otherwise records why."""
    resident = sum(
        len((entry.get("data") or b"")) for entry in _limits._CAPTURE_TRAY.values()
    )
    incoming = len(item.get("data") or b"")
    if resident + incoming > _limits.tray_max_bytes():
        _note_eviction(
            cid,
            "still on the carry-forward shelf: it did not fit the tray's "
            "byte budget beside this call's other screens -- send fewer "
            "capture_ids, or re-capture it",
        )
        return False
    return True


def _revive_one_capture(cid: str) -> bool:
    """Make *cid* resolve in the tray; True when it does, False when it cannot."""
    if cid in _limits._CAPTURE_TRAY:
        return True
    item = _limits._CARRY_SHELF.get(cid)
    if not item:
        return False
    if not _fits_tray(cid, item):
        return False
    _limits._CARRY_SHELF.pop(cid, None)
    _limits._CAPTURE_EVICTIONS.pop(cid, None)
    _limits._CAPTURE_TRAY[cid] = item
    return True


def _revive_loop(ids: list, revived: list) -> None:
    """Revive the capped slice of *ids*; each id that resolves joins *revived* as it goes."""
    for cid in ids[: _limits.tray_max()]:
        if _revive_one_capture(cid):
            revived.append(cid)


# NORMALISE, THEN cap -- in that order. This used to slice the RAW list
# first, while peek_captures and resolvable_captures both strip blanks
# and collapse duplicates before counting. A caller sending 24 blanks or
# repeats followed by a real shelved id therefore had it revived by
# neither, while the probes still reported it resolvable: the hint went
# silent and the gate asked. Same list, same order, same cap, or the
# shared predicate is shared in name only (third-round review, M1).
# And the answer is what RESOLVES, not what was attempted: a sweep
# between the loop and the return (TTL, count cap) could still drop an
# id, and a caller told 'recovered 8' that can only peek 2 is the
# failure this whole path exists to prevent.
def revive_captures(capture_ids: list | None) -> list:
    """Move shelved screens BACK into the tray under their ORIGINAL ids.

    Returns the ids that now resolve, in order -- a tray-resident id is included
    untouched, so this is a no-op for a live tray. The original ``created_at`` is
    preserved, so a revived screen keeps expiring on its own capture clock
    instead of getting a fresh TTL, and everything downstream (peek_captures,
    the labels, the _select_prepare_images byte budget, _drop_captures) is
    byte-identical to a host that re-sent the ids itself. Never raises."""
    revived: list = []
    with never_raise(logger, "reviving captured screens failed", exc_info=True):
        _sweep_carry_shelf()
        normalised = [cid for cid in _normalised_ids(capture_ids) if cid]
        _revive_loop(normalised, revived)
        _sweep_capture_tray()
        revived = [cid for cid in revived if cid in _limits._CAPTURE_TRAY]
    return revived


# The ids that would resolve to real screen BYTES right now, WITHOUT moving
# anything: tray-resident, or still recoverable off the carry-forward shelf.
#
# 2026-08-09 (review H1/M3). The re-prepare image precondition has to know what
# a call actually carries, and it has to know it BEFORE the dismissible
# clarifies that may end the call -- a probe that mutates turns the retry into
# a different call. Sweeps first, so an EXPIRED shelf entry is never reported
# as resolvable (that is exactly the case that shipped an imageless payload in
# silence). Never raises; on any internal error it reports NOTHING resolvable,
# which fails toward the disclosure rather than away from it: the cost is one
# refusal the tester clears with `image_carry_ack=true`, against a silent
# imageless generation on the other side.
def resolvable_captures(capture_ids: list | None) -> list:
    """The ids that would resolve to real screen BYTES right now, WITHOUT moving anything."""
    out: list = []
    with never_raise(logger, "resolvable_captures failed", exc_info=True):
        _sweep_capture_tray()
        # DEDUPED before the cap slice (2026-08-09, review M1): counting the same
        # id twice told the re-prepare precondition this call carried two screens
        # when it carried one -- a silent loss by arithmetic.
        wanted = _normalised_ids(capture_ids)
        for cid in wanted[: _limits.tray_max()]:
            if cid and (cid in _limits._CAPTURE_TRAY or cid in _limits._CARRY_SHELF):
                out.append(cid)
    return out


def _resent_ids(capture_ids: list | None) -> list:
    """The stripped, non-empty ids of a re-sent list (duplicates kept)."""
    return [
        str(x or "").strip() for x in list(capture_ids or []) if str(x or "").strip()
    ]


def _resent_note(count: int) -> str:
    """The tester-facing note for *count* ids re-used off the shelf."""
    return (
        f"> \U0001f4f8 Re-used {count} capture id(s) that an earlier"
        " preparation for this source had already shipped: the screens were"
        " still held server-side, so this preparation is grounded on the SAME"
        " screens instead of reporting those ids as expired."
    )


# RE-SENT ids: report what a previous prepare already shipped and this call
# can pull back. ``(capture_ids, revivable_from_shelf, note)``.
#
# Both tool docstrings tell the host to re-send the SAME `capture_ids`
# unchanged, and once a prepare ships them they live on the shelf rather than
# in the tray -- so without this they look SUPPLIED (which suppresses the
# carry-forward check entirely) while resolving to NOTHING, and the prep ships
# imageless with only a generic "unknown or expired id" note. That is the same
# silent loss this whole guard exists to stop, arrived at from the other
# direction.
#
# PROBE ONLY since 2026-08-09 (review M3): it used to revive here, several
# early returns before the call was certain to proceed, so a dismissible
# clarify consumed the shelf and the retry -- whose ids were now tray-resident
# -- reported nothing revived and stamped carried_forward_capture_count 0. The
# caller commits the revive once, past both clarifies.
#
# The id list is returned UNCHANGED: a genuinely unknown id must survive to be
# DISCLOSED rather than quietly filtered out. A tray-resident id counts as
# nothing revived. Never raises.
def revive_resent_captures(capture_ids: list | None) -> tuple:
    """RE-SENT ids: what a previous prepare shipped and this call can pull back."""
    with never_raise(logger, "revive_resent_captures failed", exc_info=True):
        wanted = _resent_ids(capture_ids)
        if not wanted:
            return (capture_ids, [], "")
        # Sweep first: an expired shelf entry must not be announced as re-used.
        _sweep_capture_tray()
        from_shelf = [
            c
            for c in wanted
            if c in _limits._CARRY_SHELF and c not in _limits._CAPTURE_TRAY
        ]
        if not from_shelf:
            return (capture_ids, [], "")
        return (capture_ids, list(from_shelf), _resent_note(len(from_shelf)))
    return (capture_ids, [], "")
