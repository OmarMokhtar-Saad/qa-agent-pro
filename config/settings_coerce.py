"""Lenient boolean coercion for the settings modules (split out of settings.py).

Imports nothing internal. Every settings coercer logs to the one
``qa_agents.settings`` logger, which lives here so all modules share it.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger("qa_agents.settings")

TRUTHY_TOKENS = ("1", "true", "yes", "on")
FALSY_TOKENS = ("0", "false", "no", "off", "")

# lenient_bool parses a bool the same way the pre-pydantic settings did and
# never raises. An unparseable value resolves to ``default`` -- the field's OWN
# declared default, passed in by the facade's _coerce_bool -- not to a
# hard-coded False. That distinction is the whole point: every ``*_DRY_RUN``
# flag defaults to True, so a hard-coded False fallback meant a blank or
# mistyped value silently DISARMED the guard it was meant to preserve
# (``TESTRAIL_DRY_RUN=`` in a .env opened a live external write). Two
# unparseable cases, both logged:
#
# - Blank (``FLAG=`` or whitespace): an env var that is present but empty is
#   ambiguous config, not an explicit false, so it reads as unset and the field
#   default wins. This is why "" is handled BEFORE FALSY_TOKENS.
# - Unrecognised (a typo like ``treu``): same fallback, louder message.
#
# Detection, not prevention -- the never-raise contract is unchanged.


def _blank_bool(value: object, field_name: str, default: bool) -> bool:
    logger.warning(
        "Blank boolean %s=%r — a present-but-empty value reads as unset, so "
        "the field default %r applies; write an explicit %s/%s to override",
        (field_name or "setting").upper(),
        value,
        default,
        TRUTHY_TOKENS[1],
        FALSY_TOKENS[1],
    )
    return default


def _invalid_bool(value: object, field_name: str, default: bool) -> bool:
    logger.warning(
        "Invalid boolean %s=%r — expected one of %s (true) / %s (false); "
        "falling back to the field default %r",
        (field_name or "setting").upper(),
        value,
        "/".join(TRUTHY_TOKENS),
        "/".join(t for t in FALSY_TOKENS if t),
        default,
    )
    return default


def lenient_bool(value: object, field_name: str = "", default: bool = False) -> bool:
    """Parse a bool leniently -- never raises (see the comment above)."""
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in TRUTHY_TOKENS:
        return True
    if token == "":
        return _blank_bool(value, field_name, default)
    if token in FALSY_TOKENS:
        return False
    return _invalid_bool(value, field_name, default)


BOOL_FIELDS = (
    "qa_api_test_enabled",
    "qa_testrail_push_enabled",
    "qa_xray_push_enabled",
    "qa_api_framework_write_enabled",
    "qa_api_framework_write_dry_run",
    "qa_ac_anchoring_enforce",
    "jira_fetch_comments",
    "jira_fetch_images",
    "jira_fetch_parent",
    "jira_fetch_sibling_stories",
    "qa_dist_mode",
    "qa_mcp_enabled",
    "qa_update_require_signature",
    "qa_host_ambiguity_require_result",
    "qa_host_image_require_relevant",
    "qa_host_dedup_apply",
    "qa_mobile_https_capture_enabled",
    "qa_mobile_app_evidence",
)


# The fallback is the field's OWN default, never a hard-coded False: every
# *_DRY_RUN flag defaults to True, so a blank/mistyped value used to disarm the
# very guard it should have preserved. A non-bool default (or PydanticUndefined
# on a required field) degrades to False, which is the historical behaviour.
def coerce_bool(v: object, field_name: str, fields: Mapping[str, Any]) -> bool:
    raw_default = getattr(fields.get(field_name), "default", False)
    default = raw_default if isinstance(raw_default, bool) else False
    return lenient_bool(v, field_name, default)
