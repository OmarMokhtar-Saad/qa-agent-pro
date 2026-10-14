"""Whether the typed value landed in the field: yes / no / unknown."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile.ime_consts import (
    _SEPARATORS,
    LANDED_NO,
    LANDED_UNKNOWN,
    LANDED_YES,
)


# Asked only about an UNCHANGED field that does not contain the value, where
# before/after comparison has run out: it cannot separate "nothing was
# committed" from "a transformed commit landed on what was already there".
# This decides which of the two to assume.
#
# The arms are not symmetric, so neither are the errors. A false ``True``
# turns a ``no`` into an ``unknown`` and costs ONE model round trip to look
# at the screen. A false ``False`` turns a transformed commit into a ``no``,
# and the executor ENDS THE CASE -- a passing run dies. So this leans
# towards ``True``, and each clause is a shape a real field produces:
#
# * an EMPTY field is the one thing no commit can leave behind, so it is the
#   only certain ``False`` -- and it is asked FIRST, because
#   ``value.startswith("")`` is True for every value, which would otherwise
#   read an untouched empty field as a truncation;
# * ``value.startswith(after)`` is a ``maxLength`` cap, which keeps a prefix;
# * separators and case stripped from both sides catches a mask, a trim and
#   a field that upper- or lower-cases what it accepts.
def _could_be_transform(after: str, value: str) -> bool:
    """Could *after* be what this field MADE of *value*?"""
    if not after:
        return False
    if value.startswith(after):
        return True
    stripped = _SEPARATORS.sub("", after).casefold()
    return stripped == _SEPARATORS.sub("", value).casefold()


class Seen(NamedTuple):
    """The field as read before and after the commit (length or text)."""

    before: Any
    after: Any


def _secret_landed_verdict(before: int, after: int, value: str) -> str:
    """The ``landed`` verdict for a secret field, from LENGTHS only."""
    if after - before == len(value):
        return LANDED_YES
    # EMPTY after the commit is the one shape no commit leaves behind,
    # whatever the field held before -- asked before the changed/unchanged
    # split, which read a field emptied by a failed commit as ``unknown``.
    if after == 0:
        return LANDED_NO
    if after != before:
        return LANDED_UNKNOWN
    # Unchanged length, and only the length is knowable here. A
    # replace-on-focus of a field that already held something this long,
    # and a capped field that kept a truncation of what was sent, are both
    # indistinguishable from nothing happening. An EMPTY field is the one
    # shape no commit can leave behind, so it is all that is left of ``no``.
    return LANDED_NO if after == 0 else LANDED_UNKNOWN


# An empty *value* is ``unknown``: there is nothing to look for, and calling
# a no-op ``yes`` would let a step that typed nothing report success -- the
# exact defect this verdict exists to end.
#
# The SECRET arm compares LENGTHS ONLY; the plaintext is never held, diffed
# or returned. Its accepted weakness is named in docs/MOBILE_TESTING.md: a
# field holding unrelated pre-existing content of a matching length (autofill
# is the realistic case) can read as ``yes``. The non-secret arm does not
# have that weakness because it compares content.
#
# **A COMMIT CAN REPLACE, NOT ONLY APPEND.** `QaImeService` calls
# `commitText`, which overwrites the current SELECTION, and
# `replay.device_ops._perform` taps the field to focus it immediately before typing --
# the gesture that triggers `selectAllOnFocus`. So "the field did not change"
# does NOT imply "nothing happened": re-typing a value the field already
# held is a correct type with an unchanged field. Containment is therefore
# asked FIRST, and that collision resolves to ``unknown``, never ``no``.
# **AN UNCHANGED FIELD WITHOUT THE VALUE IS STILL NOT PROOF.** A field that
# TRANSFORMS what it accepts -- a phone mask, a ``maxLength`` cap, a trim, a
# case fold -- can take *value* and land on text the field already held, and
# then it holds neither its old contents unchanged by accident nor the value
# itself. Before/after comparison cannot separate that from "nothing was
# committed", so `_could_be_transform` decides which to assume and ``no``
# narrows to an unchanged field whose contents no transform of *value* could
# be. This matters because the arms are not symmetric: ``unknown`` asks the
# model to look, while ``no`` ends the case, so a false ``no`` kills a
# working run.
#
# The SECRET arm cannot ask that question at all -- only LENGTHS are knowable
# there -- so ``no`` narrows further still, to a field that is EMPTY after
# the commit. A field capped at two characters lands ``s3`` of ``s3cret``
# without moving its length, and nothing here can tell that from a dead one.
def _landed_verdict(seen: Seen, value: str, secret: bool) -> str:
    """Did *value* land in the field? ``yes`` / ``no`` / ``unknown``."""
    before, after = seen
    if before is None or after is None or not value:
        return LANDED_UNKNOWN
    if secret:
        return _secret_landed_verdict(int(before), int(after), value)
    if not str(after):
        return LANDED_NO
    if value in str(after):
        return LANDED_YES if after != before else LANDED_UNKNOWN
    if after != before:
        return LANDED_UNKNOWN
    return LANDED_UNKNOWN if _could_be_transform(str(after), value) else LANDED_NO
