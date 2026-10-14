"""Host mode: the notes for a per-category submission that carried a field this route cannot use."""

from __future__ import annotations

from config.settings_guard import never_raise

_DEDUP_NOTE = (
    "> ℹ️  `duplicate_groups` cannot be used on the per-category "
    "path: finalizing from accumulated rows merges only `test_cases` and "
    "renumbers every tc_id, so the field has no channel and per-category ids "
    "could not be mapped onto the merged suite. Duplicate review is "
    "available ONLY when you submit the whole merged suite to "
    "`qa_submit_suite`.\n\n"
)

_CHECKLIST_NOTE = (
    "> \u2139\ufe0f  `checklist_items` cannot be used on the "
    "per-category path: finalizing from accumulated rows merges only "
    "`test_cases`, so the field has no channel here and THIS copy was "
    "discarded. It is not lost to the staged route, though -- send it "
    "in the finalize review SIDECAR (a `suite_json` carrying the field "
    "and no `test_cases`) described in your preparation instructions, "
    "or with one merged suite. Send it ONCE: the server assigns every "
    "`CL-NNN` id from that single list.\n\n"
)


# One line explaining that a PER-CATEGORY submission's ``duplicate_groups``
# cannot be used at all.
#
# It is not merely "sent to the wrong tool": finalizing from accumulated rows goes
# through ``mcp_handlers.merge_category_rows``, which copies ONLY ``test_cases``
# and GLOBALLY RENUMBERS every tc_id -- so there is no channel for the field and
# stored per-category ids could not be mapped onto the merged suite even if there
# were. Duplicate review is therefore available ONLY when the whole merged suite is
# submitted to ``qa_submit_suite``. Empty (and therefore output-identical) when the
# field was absent. Never raises.
def category_dedup_note(parsed) -> str:
    """The note for a per-category ``duplicate_groups`` field, or "" when it was absent."""
    # pragma: no cover - defensive
    with never_raise():
        if not getattr(parsed, "duplicate_groups", None):
            return ""
        return _DEDUP_NOTE
    return ""


# One line for a PER-CATEGORY submission that carried ``checklist_items``.
#
# Residue R4. ``_CHECKLIST_JOB_INSTRUCTIONS`` tells the host this route cannot
# carry the field and to put it in the finalize SIDECAR instead -- but a host
# that sends it anyway had it popped by ``_validate_suite`` and structurally
# dropped by ``merge_category_rows`` (which copies ``test_cases`` only), with
# the only downstream signal a generic finalize-time "No requirements
# checklist" that explains nothing. Unlike ``duplicate_groups`` the field is
# NOT lost to this route -- the sidecar carries it -- so this note points at
# the mechanism rather than merely refusing.
#
# Empty (and therefore output-identical) when the field was absent. Never
# raises.
def category_checklist_note(parsed) -> str:
    """The note for a per-category ``checklist_items`` field, or "" when it was absent."""
    # pragma: no cover - defensive
    with never_raise():
        if getattr(parsed, "raw_checklist_items", None) is None:
            return ""
        return _CHECKLIST_NOTE
    return ""
