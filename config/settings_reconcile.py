"""Host-dedup ratio coercion for the settings modules (split out of settings.py).

Imports nothing internal except ``config.settings_scores``.
"""

from __future__ import annotations

from typing import Any, Mapping

from config.settings_scores import parse_score

RECONCILE_FIELDS = (
    "qa_host_dedup_max_removal_ratio",
    "qa_host_dedup_low_text_ratio",
)


# Lenient, never-raising float coercion for the host-dedup ratios.
#
# The NAME of the facade validator is historical: it was written for
# qa_comment_reconcile_field_threshold / _dedup_threshold, which were deleted on
# 2026-08-15 with tools/comment_reconciler.py (dead-code deletion batch D5).
# Kept rather than renamed because the two surviving fields' coercion behaviour
# is unchanged.
def coerce_reconcile_threshold(
    v: object, field_name: str, fields: Mapping[str, Any]
) -> float:
    default = fields[field_name].default
    return parse_score(v, field_name, default)[0]
