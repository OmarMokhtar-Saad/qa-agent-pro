"""Startup disclosure state of tools.host_llm (split out of host_llm.py)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools import host_llm_allow as al
from tools import host_llm_ledger as lg
from tools import host_llm_notes as nt
from tools import host_llm_unmigrated as um

logger = logging.getLogger("tools.host_llm")


def _projection() -> tuple[str, int, tuple, tuple]:
    """``(unknown_note, total, bypassed, off)`` for the disclosure branches."""
    unknown_note = nt._unknown_ids_note(al.unknown_allow_ids())
    total = len(lg.LEDGER_IDS)
    bypassed = al.allowed_and_unmigrated()
    bypassed_keys = {key for key, _ in bypassed}
    # Rows that are genuinely OFF, derived FROM pending_paths() minus the
    # allow-listed ones -- ONE rule in ONE place. pending_paths() honours only
    # EXPLICIT ids (so `*` can never empty it) and subtracting
    # allowed_and_unmigrated() removes the wildcard-bypassed rows too, which
    # makes this the wildcard-aware projection OF that single source rather
    # than a second, near-duplicate copy of the same rule.
    off = tuple((k, d) for k, d in al.pending_paths() if k not in bypassed_keys)
    return unknown_note, total, bypassed, off


# ``(note, degraded)`` -- the disclosure line PLUS an explicit degraded flag.
#
# ``degraded`` is what ``warn_once_if_degraded`` triggers on. It is a real
# boolean returned alongside the note, never a "does the note start with a
# warning sign" sniff of the string, so rewording a message can never silently
# disable the startup WARNING.
#
# FOUR states, and only ONE of them is calm:
#
# * flag ON -> ``("", False)``: nothing to disclose, today's behaviour.
# * rows OFF (unmigrated and not allow-listed) -> warning naming them,
#   ``degraded=True``.
# * every unmigrated row allow-listed -> BYPASS warning, ``degraded=True``.
#   Reached by `*` AND by an explicit list naming every row, because the
#   condition, not the literal `*`, is what makes it a false all-clear: nothing
#   migrated, everything still billing server-side.
# * ``UNMIGRATED_PATHS`` empty -> calm. TRUE completion is the only thing that
#   is allowed to look like completion.
#
# Both warning branches count against ``len(LEDGER_IDS)`` -- the CONSTANT
# 24-row ledger total -- so "2 of 24 rows" reads as progress as the migration
# advances, rather than "2 of 2" against a shrinking denominator. Never raises.
def disclosure_state() -> tuple[str, bool]:
    """``(note, degraded)`` -- the disclosure line PLUS an explicit degraded flag."""
    # defensive; disclosure must never break
    with never_raise(logger, "host_llm.disclosure failed", exc_info=True):
        if not al.server_llm_retired():
            return "", False
        unknown_note, total, bypassed, off = _projection()
        if not um.UNMIGRATED_PATHS:
            return nt._complete_note(unknown_note), False
        if off:
            return nt._off_note(
                off, nt._Tally(len(bypassed), total), unknown_note
            ), True
        # off is empty while UNMIGRATED_PATHS is not: the allow-list covers every
        # unmigrated row, so the kill switch is bypassed and NOTHING migrated.
        # This must never look like the calm branch above.
        return nt._bypassed_note(len(bypassed), total, unknown_note), True
    return "", False


def disclosure() -> str:
    """The disclosure line for qa-doctor, without the degraded flag.

    Empty string when there is nothing to disclose (the flag is ON, i.e. today's
    behaviour). See ``disclosure_state`` for the four states. Never raises.
    """
    return disclosure_state()[0]
