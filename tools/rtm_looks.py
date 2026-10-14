"""Heuristic: could a string be a testable acceptance criterion?"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("tools.rtm")

# Values that are NOT acceptance criteria even when a configured field returns
# them. `settings.jira_ac_field` defaults to customfield_10016, which is a DATE
# field on some Jira instances: TICKET-5645 returned
# "2025-09-11T09:07:21.362+0300", that truthy value suppressed description
# scanning, and the generator was handed a timestamp as its only requirement to
# trace against -- so nothing downstream could tell a grounded case from an
# invented one.
_ISO_DATEISH_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
_SLASH_DATEISH_RE = re.compile(r"^\d{1,4}[/.]\d{1,2}[/.]\d{1,4}$")
_NUMERIC_RE = re.compile(r"^[\d.,%+-]+$")


def _sentence_shaped(value: str) -> bool:
    """True when a stripped, non-empty value passes every sentence-shape check."""
    if _ISO_DATEISH_RE.match(value) or _SLASH_DATEISH_RE.match(value):
        return False
    if _NUMERIC_RE.match(value):
        return False
    if not re.search(r"[^\W\d_]", value, re.UNICODE):
        return False
    # A requirement is a sentence; a single unbroken token is an id, a date,
    # or a label -- never a testable condition.
    return bool(re.search(r"\s", value))


# Deliberately NARROW -- it rejects only what cannot possibly be a
# requirement, because a false rejection silently discards a real criterion:
#
# * a date or ISO-8601 timestamp (the observed failure);
# * a value with no whitespace at all -- a requirement is a sentence, and this
#   is what separates a timestamp or a field id from prose;
# * a purely numeric/punctuation value;
# * a value with no letters in any script.
#
# Multi-word criteria that merely START with a number are UNAFFECTED, so
# NB-005 behaviour ("200ms response time", "3 failed logins locks the
# account") is preserved. Never raises.
def looks_like_requirement_text(text: object) -> bool:
    """True when a string could be a testable acceptance criterion."""
    try:
        if not isinstance(text, str):
            return False
        value = text.strip()
        if not value:
            return False
        return _sentence_shaped(value)
    except Exception:
        logger.exception(
            "looks_like_requirement_text failed - treating as non-requirement"
        )
        return False
