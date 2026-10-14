"""Host mode: the requirements-checklist disclosure section (split from host_checklist.py)."""

from __future__ import annotations

import logging

from agents.host_checklist_result import _CL_MAX_NOTES
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


def _checklist_not_ran_block(carried: int) -> str:
    """The block for a submission that carried no usable checklist field."""
    if carried:
        return (
            "> ℹ️  **Requirements checklist: MODEL-DERIVED "
            f"(carried forward).** This submission carried no "
            f"`checklist_items` field, so the {carried} requirement(s) "
            "your chat model decomposed on an earlier round of THIS "
            "prep are still in force and were used for the coverage "
            "tally. You do not need to resend them; if you DO send the "
            "field again it replaces them wholesale.\n\n"
        )
    return (
        "> ℹ️  **No requirements checklist.** This server did not "
        "decompose the ticket -- that decomposition runs in your chat, "
        "not on this server -- and the submission carried no usable "
        "`checklist_items` field, so there is NO requirement coverage "
        "tally for this run. Nothing was invented to fill the gap.\n\n"
    )


def _granularity_line(audit: dict) -> str:
    """The granularity-audit score line."""
    score = audit.get("score", "")
    return (
        f"> Granularity score: **{score}** over "
        f"{audit.get('item_count', 0)} item(s)"
        + ("" if audit.get("passed", True) else " -- BELOW the threshold")
        + "."
    )


def _checklist_ran_block(result, audit: dict | None) -> str:
    """The block for an accepted checklist: head, optional audit line, notes."""
    lines = [
        "> \u2139\ufe0f  **Requirements checklist: MODEL-DERIVED.** "
        f"{len(result.items)} atomic requirement(s) were decomposed by YOUR "
        "chat model, not by this server, and the ids (CL-001 ...) were "
        "assigned here. This server's own check over your list is the "
        "pure-Python granularity audit, which is exactly the detector for "
        "a narrow or inflated decomposition."
    ]
    if isinstance(audit, dict) and audit:
        lines.append(_granularity_line(audit))
    for note in list(getattr(result, "notes", None) or [])[:_CL_MAX_NOTES]:
        lines.append(f"> - {note}")
    return "\n".join(lines) + "\n\n"


# Disclosure block for a host-authored requirements checklist.
#
# Returns "" when the job was never requested, so a submit for a prep that
# shipped no CHECKLIST_JOB is byte-identical to today.
#
# ``carried`` is the number of requirements this prep ALREADY holds from an
# earlier round of the same flow (residue R4: the gap-remediation loop persists
# the adopted checklist back into the prep envelope, so round 2+ rehydrates it
# instead of losing it). It only changes the wording when the CURRENT
# submission carried no usable field: "no checklist" would then be a FALSE
# claim, and the reply must say the earlier one is still in force rather than
# invite the host to resend it. Never raises.
def build_host_checklist_section(
    result, audit: dict | None = None, *, carried: int = 0
) -> str:
    """Disclosure block for a host-authored requirements checklist."""
    # pragma: no cover - defensive
    with never_raise(logger, "build_host_checklist_section failed", exc_info=True):
        if result is None or not getattr(result, "requested", False):
            return ""
        if not getattr(result, "ran", False):
            return _checklist_not_ran_block(carried)
        return _checklist_ran_block(result, audit)
    return ""
