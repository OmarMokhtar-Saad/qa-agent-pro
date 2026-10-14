"""Host mode: schema version and field tables of the prep serializer."""

from __future__ import annotations

# Bump this whenever the serialized shape changes incompatibly. A prep record
# persists in SQLite across server restarts AND across auto-updates (users
# auto-update from GitHub Releases), so a record written by an older build can be
# read by a newer one. deserialize_prepared REJECTS any other version rather than
# half-rehydrating a mismatched shape -- the caller (ops-3d) then treats it
# exactly like a stale/unknown prep_id.
_SCHEMA_VERSION = 1

# Every field of PreparedGeneration this serializer knows how to represent. If a
# future field is added to the dataclass and NOT added here, serialize_prepared
# raises PrepSerializeError instead of silently dropping it, and
# tests/test_prep_serialization.py asserts this set equals
# dataclasses.fields(PreparedGeneration) -- two independent guards against a
# silently-forgotten field.
_KNOWN_FIELDS = frozenset(
    {
        "user_msg",
        "rtm_hint",
        "feature_text",
        "complexity_text",
        "acs",
        "source_acs",
        "checklist_items",
        "checklist_presented_ids",
        "checklist_audit",
        "rule_packs",
        "ui_content",
        "parent_context",
        "cache_prefix_warm",
        "jira_image_text",
        "attached_image_text",
        "jira_context_text",
        "image_notice",
        "categories",
        "category_response_schema",
        "target_description",
    }
)

# Plain string fields carried VERBATIM (JSON-native already).
_STR_FIELDS = (
    "user_msg",
    "rtm_hint",
    "feature_text",
    "complexity_text",
    "parent_context",
    "jira_image_text",
    "attached_image_text",
    "jira_context_text",
    "image_notice",
    "target_description",
)

# bool / dict|None fields carried VERBATIM (JSON-native already).
_VERBATIM_FIELDS = (
    "ui_content",
    "checklist_audit",
    "category_response_schema",
    "cache_prefix_warm",
)
