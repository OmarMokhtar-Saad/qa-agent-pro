"""Int-field bounds for the settings modules (split out of settings.py).

Imports nothing internal except ``config.settings_coerce`` (the shared logger).
"""

from __future__ import annotations

from typing import Any, Mapping

from config.settings_coerce import logger

# Int fields that are nonsensical at <= 0 (timeouts, loop-bound counts,
# concurrency). A parseable but out-of-range value (e.g. QA_LLM_TIMEOUT_S=0/-5)
# is logged and replaced with the field default HERE, so downstream code
# (llm._int_setting) needs no silent second clamp.
# Deliberately EXCLUDED:
#   * qa_rag_recency_half_life_days / qa_rag_max_entries — 0 is a documented
#     "disable" / "unlimited" sentinel.
#   * the byte/char/image/comment CAPS (jira_max_comments, jira_max_images,
#     jira_max_image_bytes) — 0 there is a legitimate "allow none" cap;
#     bounding them would be a behaviour change out of scope for this
#     hygiene batch. qa_max_chat_images / qa_max_chat_image_bytes were named
#     here until 2026-08-30, when both fields were deleted with the last of
#     the backend settings.
#     qa_max_spec_bytes / qa_max_spec_chars were on this list until
#     2026-08-15, when batch D1 deleted them with tools/doc_ingest.py.
POSITIVE_INT_FIELDS = frozenset(
    {
        "qa_checklist_max_items",
        "qa_checklist_max_prompt_chars",
        "qa_device_command_timeout",
        "qa_device_screenshot_timeout",
        "qa_update_timeout",
        "qa_host_dedup_max_groups",
        "qa_host_dedup_max_group_size",
    }
)

JIRA_INT_FIELDS = (
    "jira_max_comments",
    "jira_max_images",
    "jira_max_image_bytes",
    "jira_max_context_chars",
    "jira_max_ac_chars",
    "jira_max_parent_chars",
    "jira_max_sibling_chars",
    "jira_max_sibling_stories",
    "qa_device_command_timeout",
    "qa_device_screenshot_timeout",
    "qa_rag_recency_half_life_days",
    "qa_rag_max_entries",
    "qa_update_timeout",
    "qa_checklist_max_items",
    "qa_checklist_max_prompt_chars",
    "qa_prep_ttl_s",
    "qa_prep_max_bytes",
    "qa_prep_max_lifetime_s",
    "qa_host_dedup_max_groups",
    "qa_host_dedup_max_group_size",
    "qa_host_duplicate_prep_window_s",
    "qa_mobile_boot_timeout_s",
)


def coerce_top_k(v: object) -> int:
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    try:
        return int(str(v).strip())
    except (TypeError, ValueError, OverflowError):
        logger.warning("Invalid QA_RAG_TOP_K=%r — using default 5", v)
        return 5


def _parse_int(v: object, field_name: str, default: int) -> int | None:
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    try:
        return int(str(v).strip())
    except (TypeError, ValueError, OverflowError):
        logger.warning(
            "Invalid %s=%r — using default %d", field_name.upper(), v, default
        )
        return None


def coerce_jira_int(v: object, field_name: str, fields: Mapping[str, Any]) -> int:
    default = fields[field_name].default
    parsed = _parse_int(v, field_name, default)
    if parsed is None:
        return default
    if field_name in POSITIVE_INT_FIELDS and parsed < 1:
        logger.warning(
            "Invalid %s=%r (must be > 0) — using default %d",
            field_name.upper(),
            parsed,
            default,
        )
        return default
    return parsed
