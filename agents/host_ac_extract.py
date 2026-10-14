"""Host mode: read the untrusted acceptance-criteria field into a HostACResult (split from host_ac.py)."""

from __future__ import annotations

import logging

from agents.host_ac_result import _AC_MAX_ITEMS, HostACResult, _ac_note
from agents.host_ac_stage import _assign_ac_ids, _stage_ac_entries
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


def _finish_ac_result(res: HostACResult, out: list) -> HostACResult:
    """Mark the result ran with ``out``, or note that nothing usable survived."""
    if not out:
        _ac_note(
            res,
            "`acceptance_criteria` contained no usable criterion, so it is "
            "treated as an UNUSABLE field. Nothing was invented to replace "
            "it: this run has no requirements traceability.",
        )
        return res
    res.acs = out
    res.ran = True
    logger.info(
        "host-derived acceptance criteria: %d kept, %d dropped, %d reassigned "
        "-- MODEL-DERIVED, not ticket-sourced",
        len(out),
        res.dropped,
        res.reassigned,
    )
    return res


def _note_ac_adjustments(res: HostACResult) -> None:
    """Disclose dropped entries and reassigned ids."""
    if res.dropped:
        _ac_note(
            res,
            f"{res.dropped} entr(ies) in `acceptance_criteria` were not "
            "usable criteria and were dropped.",
        )
    if res.reassigned:
        _ac_note(
            res,
            f"{res.reassigned} criterion id(s) were missing, malformed or "
            "duplicated and were REASSIGNED in order. A test case tagged "
            "with one of those ids may now trace to a different criterion.",
        )


def _bounded_ac_entries(raw, res: HostACResult) -> list | None:
    """The entries to read (capped at _AC_MAX_ITEMS), or None for absent/non-list."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        _ac_note(
            res,
            "`acceptance_criteria` was not a list -- the whole field was "
            "ignored. No criteria were derived and none were invented.",
        )
        return None
    entries = list(raw)
    if len(entries) > _AC_MAX_ITEMS:
        _ac_note(
            res,
            f"`acceptance_criteria` carried {len(entries)} entries -- only "
            f"the first {_AC_MAX_ITEMS} were read.",
        )
        entries = entries[:_AC_MAX_ITEMS]
    return entries


def _ac_unreadable(requested: bool) -> HostACResult:
    """The result for a field that could not be read at all."""
    return HostACResult(
        requested=bool(requested),
        notes=[
            "`acceptance_criteria` could not be read -- it was ignored, and "
            "no criteria were invented to replace it."
        ],
    )


# Validate the SHAPE of the UNTRUSTED top-level `acceptance_criteria` field.
#
#     NEVER raises and NEVER trusts the field. Rules, enforced here in Python over
#     already-``json.loads``'d data (no eval, no ast, no dynamic attribute access):
#
#       * absent / None              -> ran=False, no notes (the common case)
#       * not a list                 -> ran=False + note
#       * a string entry             -> tolerated as its description
#       * a dict entry               -> `description` (or `text`), optional `ac_id`
#       * any other entry type       -> dropped + counted
#       * a description under 5 chars-> dropped (mirrors parse_acceptance_criteria)
#       * a duplicate description    -> collapsed silently
#       * beyond _AC_MAX_ITEMS       -> truncated + noted
#       * an id that is not AC-NNN,
#         or one already used        -> REASSIGNED to the next free positional id
#         and counted. Ids are never trusted as given: a colliding or invented id
#         would silently re-point another case's requirement_id.
#       * ZERO surviving criteria    -> ran=False + note
#
#     Ids that DO canonicalise (via rtm.normalize_ac_id, so AC-1 / ac001 / AC-001
#     all land on AC-001) are kept, which is what keeps the host's own
#     `requirement_id` tags pointing at the right criterion.
#
def extract_host_acs(raw, *, requested: bool = True) -> HostACResult:
    """Validate the SHAPE of the UNTRUSTED top-level `acceptance_criteria` field."""
    res = HostACResult(requested=bool(requested))
    with never_raise(
        logger,
        "could not read acceptance_criteria -- ignoring the field",
        level=logging.WARNING,
        exc_info=True,
    ):
        entries = _bounded_ac_entries(raw, res)
        if entries is None:
            return res
        staged = _stage_ac_entries(entries, res)
        out = _assign_ac_ids(staged, res)
        _note_ac_adjustments(res)
        return _finish_ac_result(res, out)
    return _ac_unreadable(requested)
