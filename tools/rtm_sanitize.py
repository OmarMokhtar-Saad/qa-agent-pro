"""Acceptance-criterion description sanitising and the AcceptanceCriterion record."""

from __future__ import annotations

from dataclasses import dataclass

from tools import rtm_consts as _k
from tools.untrusted import strip_spoof_tags


def sanitize_ac_description(raw: object) -> str:
    """Strip forged <untrusted_content> delimiters from a criterion and cap it.

    Never raises: non-string input is coerced with ``str()`` first, matching the
    lenient contract of every other parser in this module.
    """
    text = raw if isinstance(raw, str) else str(raw or "")
    cleaned = strip_spoof_tags(text)
    if len(cleaned) > _k._AC_DESCRIPTION_LIMIT:
        cleaned = cleaned[: _k._AC_DESCRIPTION_LIMIT].rstrip() + " ...[truncated]"
    return cleaned


@dataclass
class AcceptanceCriterion:
    ac_id: str
    description: str

    def __post_init__(self) -> None:
        self.description = sanitize_ac_description(self.description)
