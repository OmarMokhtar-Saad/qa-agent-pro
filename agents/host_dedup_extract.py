"""Shape validation of the UNTRUSTED ``duplicate_groups`` field."""

from __future__ import annotations

import logging

from agents.host_dedup_consts import (
    _CLAIMED_NOTE,
    _MAX_DUP_NOTES,
    _NONSTR_NOTE,
    _NOT_A_LIST_NOTE,
    _NOT_LIST_ENTRY_NOTE,
    _OVERSIZE_NOTE,
    _TOO_FEW_NOTE,
    _TOO_MANY_NOTE,
    _UNKNOWN_NOTE,
    _UNREADABLE_NOTE,
    logger,
)
from agents.host_dedup_limits import _dup_group_caps
from config.settings_guard import never_raise


def _append_note(notes: list, msg: str) -> None:
    if len(notes) < _MAX_DUP_NOTES:
        notes.append(msg)


def _clean_member(m: object, known: set, notes: list) -> str | None:
    """The stripped tc_id, or None when it is not a known string (the drop is noted)."""
    if not isinstance(m, str):
        _append_note(notes, _NONSTR_NOTE)
        return None
    tid = m.strip()
    if tid not in known:
        # Strip backticks/newlines: this id is UNTRUSTED host text
        # interpolated inside a backtick span, and a crafted value
        # could otherwise break out of it.
        safe_tid = tid[:32].replace("`", "").replace("\n", " ")
        _append_note(notes, _UNKNOWN_NOTE.format(safe_tid))
        return None
    return tid


def _collect_group_members(entry: list, scope: tuple, notes: list) -> list:
    """The valid, unclaimed, de-duplicated tc_ids of one group entry (noting drops).
    ``scope`` is ``(known, claimed, max_size)``."""
    known, claimed, max_size = scope
    members: list = []
    for m in entry:
        tid = _clean_member(m, known, notes)
        if tid is None:
            continue
        if tid in members:
            continue  # Self-reference / repeat inside one group: collapse silently.
        if tid in claimed:
            _append_note(notes, _CLAIMED_NOTE.format(tid))
            continue
        if len(members) >= max_size:
            _append_note(notes, _OVERSIZE_NOTE.format(max_size))
            break
        members.append(tid)
    return members


def _entry_group(entry: object, scope: tuple, notes: list) -> list | None:
    """The member list of one ``duplicate_groups`` entry, or None when it is skipped (noted)."""
    if not isinstance(entry, list):
        _append_note(notes, _NOT_LIST_ENTRY_NOTE)
        return None
    members = _collect_group_members(entry, scope, notes)
    if len(members) < 2:
        if members:
            _append_note(notes, _TOO_FEW_NOTE)
        return None
    return members


def _walk_duplicate_groups(raw: list, valid_ids, notes: list) -> list:
    """Shape-validate each entry of the ``duplicate_groups`` list, noting drops."""
    max_groups, max_size = _dup_group_caps()
    known = {str(i) for i in (valid_ids or ())}
    claimed: set = set()
    scope = (known, claimed, max_size)
    groups: list = []
    if len(raw) > max_groups:
        _append_note(notes, _TOO_MANY_NOTE.format(len(raw), max_groups))
    for entry in raw[:max_groups]:
        members = _entry_group(entry, scope, notes)
        if members is not None:
            claimed.update(members)
            groups.append(members)
    return groups


# Validate the SHAPE of the UNTRUSTED top-level ``duplicate_groups`` field.
#
# Returns ``(groups, notes)``: groups is a list of lists of tc_ids that all EXIST
# in the submitted suite; notes explains every rejection so the reply can say what
# was ignored. NEVER raises and NEVER trusts the field -- an unreadable value
# degrades to "no dedup" plus a note.
#
# This is shape validation ONLY, and it is explicitly NOT a safety bound: the caps
# below permit ``50 x 12 = 550`` removable ids, and the overlap rule only stops
# groups from CHAINING, so nothing here prevents a DISJOINT PARTITION of the suite.
# ``screen_duplicate_groups`` carries the bounds that gate removal; both run before
# anything is deleted.
#
# Rules, all enforced here in Python over already-``json.loads``'d data (no eval,
# no ast, no dynamic attribute access):
#
#   * absent / ``None``               -> ``([], [])`` -- the common case
#   * not a list                      -> ``([], [note])``
#   * a non-list group                -> skipped + noted
#   * a non-str member                -> dropped + noted
#   * an id not in the suite          -> dropped + noted (hallucinated / stale)
#   * a repeated id inside one group (INCLUDING a self-reference) -> collapsed
#   * an id already claimed by an EARLIER group -> dropped + noted, so
#     overlapping groups cannot chain into "the whole suite is one duplicate"
#   * fewer than 2 distinct known ids -> the group is a no-op, dropped + noted
#   * beyond the group / group-size / note caps -> truncated + noted
def _extract_duplicate_groups(raw, valid_ids) -> tuple[list, list]:
    """Validate the SHAPE of the UNTRUSTED top-level ``duplicate_groups`` field."""
    notes: list = []
    # hostile iterables may raise anything
    with never_raise(
        logger,
        "could not read duplicate_groups -- ignoring the field",
        level=logging.WARNING,
        exc_info=True,
    ):
        if raw is None:
            return [], []
        if not isinstance(raw, list):
            return [], [_NOT_A_LIST_NOTE]
        groups = _walk_duplicate_groups(raw, valid_ids, notes)
        return groups, notes[:_MAX_DUP_NOTES]
    return [], [_UNREADABLE_NOTE]
