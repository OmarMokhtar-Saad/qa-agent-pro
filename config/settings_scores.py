"""Finite-float score parsing for the settings modules (split out of settings.py).

Imports nothing internal except ``config.settings_coerce`` (the shared logger).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from config.settings_coerce import logger


def _to_float(v: object) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return float(str(v).strip())


# The finiteness guard in parse_score:
# A string never overflows float(), it returns inf or nan -- and
# nan fails EVERY comparison, so a range check alone lets it
# through. QA_RAG_SIMILARITY_THRESHOLD=nan made every
# `score >= threshold` False and turned RAG grounding off in
# silence, because the coercer had succeeded.
def parse_score(v: object, field_name: str, default: float) -> tuple[float, bool]:
    """Parse a finite float; ``(value, ok)``, ok False if bad (logged, default)."""
    try:
        parsed = _to_float(v)
    except (TypeError, ValueError, OverflowError):
        logger.warning(
            "Invalid %s=%r — using default %s", field_name.upper(), v, default
        )
        return default, False
    if not math.isfinite(parsed):
        logger.warning(
            "Invalid %s=%r (not a finite number) — using default %s",
            field_name.upper(),
            v,
            default,
        )
        return default, False
    return parsed, True


def clamp_score(parsed: float, field_name: str) -> float:
    """Clamp a finite score to [0.0, 1.0], logging when it was outside."""
    # A TOTAL comparison rather than `parsed < 0.0 or parsed > 1.0`, which NaN
    # defeats by failing both halves. Defence in depth only, and measured as
    # such: with the finiteness guard in parse_score running first, reverting
    # this line to the partial form changes nothing and no test can kill it.
    # It is here for the day the guards are reordered, not as a bound.
    if not (0.0 <= parsed <= 1.0):
        clamped = min(1.0, max(0.0, parsed))
        logger.warning(
            "%s=%r is outside the valid [0, 1] range — clamping to %s",
            field_name.upper(),
            parsed,
            clamped,
        )
        return clamped
    return parsed


CHECKLIST_FLOAT_FIELDS = (
    "qa_rag_similarity_threshold",
    "qa_checklist_min_granularity",
)


# Lenient, never-raising float coercer for the checklist granularity gate and
# the RAG similarity threshold.
#
# QA_RAG_SIMILARITY_THRESHOLD joined this group rather than keeping its own
# validator, which had no range check at all. Measured: a finite, perfectly
# parseable QA_RAG_SIMILARITY_THRESHOLD=75 made every `score >= threshold`
# False -- the same silent grounding shutdown the finiteness guard in
# parse_score was written for, reached through a wider door on the same field.
# It is a similarity SCORE, so the property that binds it binds the others.
#
# Mirrors coerce_jira_int: an unparseable value is logged and replaced with the
# field's declared default rather than raising. All the fields are similarity /
# quality SCORES, so the parsed value is additionally CLAMPED to [0.0, 1.0] --
# the same kind of range guard POSITIVE_INT_FIELDS gives the int caps. Without
# it, an operator writing QA_CHECKLIST_MATCH_HIGH=75 (meaning "75%") would
# silently push every requirement below the threshold and the report would
# claim 0% coverage for a perfectly good suite.
def coerce_checklist_float(
    v: object, field_name: str, fields: Mapping[str, Any]
) -> float:
    default = fields[field_name].default
    parsed, ok = parse_score(v, field_name, default)
    return clamp_score(parsed, field_name) if ok else default
