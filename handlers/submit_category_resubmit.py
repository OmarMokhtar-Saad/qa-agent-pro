"""The resubmit disclosure: a note when a staged category row was REPLACED."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import _reply
from handlers.submit_category_types import _CategoryArgs, _CategoryIntake

logger = logging.getLogger("tools.mcp_handlers")


def _resubmit_delta_text(prior: int, new: int) -> str:
    """Three-way case-count phrase (review C2): a shrink must never read "up from"."""
    if new < prior:
        return (
            f"{new} case(s) -- **DOWN from {prior}**; {prior - new} "
            "previously staged case(s) were dropped"
        )
    if new == prior:
        return "the same number of cases"
    return f"{new} case(s), up from {prior}"


def _rework_text(cat: str, prior: int, new: int) -> str:
    """The "this REPLACED an already-staged row" note body."""
    _delta = _resubmit_delta_text(prior, new)
    return (
        "> ⚠️  **This REPLACED an already-staged row -- it was "
        f"rework.** **{cat}** was already staged with {prior} "
        f"case(s); this submission carries {_delta}, and is now the stored "
        "version. Re-generating a category that is already staged costs "
        "minutes of chat time and usually changes nothing. Check "
        "`qa_prep_status` before generating a category, and do NOT "
        "re-submit a category unless a reply asked you to.\n\n"
    )


def _replaced_larger_row_note(cat: str, prior: int, new: int) -> str:
    """The note for a replace_smaller=true call that dropped validated cases."""
    return (
        "> ⚠️  **Replaced a larger staged row on purpose.** "
        f"**{cat}** held {prior} case(s) and now holds {new}: "
        f"`replace_smaller=true` was passed, so {prior - new} "
        "already-validated case(s) were DROPPED. Nothing else "
        "changed.\n\n"
    )


# Disclose that THIS submission REPLACED an already-staged category row.
#
# A DISCLOSURE, never a gate: the save has already been decided and this only
# names what happened. "" for a first submit (prior == 0). Never raises -- an
# advisory must not break a submit (same contract as all_staged_banner).
#
# TWO different gatings, deliberately (review C1):
#
# * The ``overridden`` note (a replace_smaller=true call that DID drop
#   validated cases) is returned BEFORE the stamp check, so it is
#   unconditional: it follows from the GUARD, not the note flag, and gating
#   it on the flag made a destructive drop SILENT.
# * The ordinary rework note IS keyed off the prep's META STAMP
#   (``host_category_resubmit_note``), never a live flag, so a mid-flow .env
#   flip cannot change an in-flight prep and an OLD envelope is unchanged.
def category_resubmit_note(args: _CategoryArgs, intake: _CategoryIntake) -> str:
    """Disclose that THIS submission REPLACED an already-staged category row."""
    meta, prior, new = intake.meta, intake.prior, len(intake.cases_json)
    with never_raise(logger, "category resubmit note failed", exc_info=True):
        _cat = _reply._clip_echo(args.category_name, 60)  # same invariant; see above
        if prior <= 0:
            return ""
        if intake.overridden:
            return _replaced_larger_row_note(_cat, prior, new)
        if not isinstance(meta, dict) or not meta.get("host_category_resubmit_note"):
            return ""
        return _rework_text(_cat, prior, new)
    return ""
