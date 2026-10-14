"""Message-template matchers built from the source (leaf of oracle_grounding)."""

from __future__ import annotations

import logging
import re

from tools.oracle_grounding_spans import _quoted_spans

logger = logging.getLogger("tools.oracle_grounding")

# A source that promises a message TEMPLATE promises every instance of it. The
# 2026-08-30 run flagged 'Add GBP 7.50 more to use this code' and
# 'Add GBP 0.01 more to use this code' as ungrounded against an acceptance
# criterion that literally reads "Add GBP {amount} more to use this code" --
# two false positives that tell a tester to LOOSEN a correct assertion. Each
# placeholder becomes a bounded wildcard; the rest of the template is escaped,
# so nothing but the placeholder is fuzzy.
_PLACEHOLDER_RE = re.compile(r"\{[^{}\n]{1,40}\}")
_MAX_TEMPLATES = 60


def _template_regex(span: str) -> re.Pattern[str] | None:
    span = span.strip()
    if not span or not _PLACEHOLDER_RE.search(span):
        return None
    parts = [re.escape(p) for p in _PLACEHOLDER_RE.split(span)]
    if len(parts) < 2:
        return None
    try:
        return re.compile(".{0,40}?".join(parts))
    except re.error:
        return None


# Built from the source's own QUOTED spans, not from the sentence around them:
# a criterion reads "the app shows 'Add GBP {amount} more to use this code'",
# and the assertion under test quotes only the message. A matcher built from
# the whole sentence carries the "the app shows" prefix and can never match the
# instance -- which is what the first cut of this fix did, and it left two of
# the five measured false positives standing.
#
# Matching is `fullmatch`: a template promises the WHOLE message, so an
# invented string that merely contains a promised fragment stays reported.
#
# Operates on the NORMALIZED grounding text, so a caller compiles once per
# suite. Never raises -- an unusable template is skipped, not reported.
def _template_patterns(grounding: str) -> list:
    """Compiled matchers for every `{placeholder}`-bearing QUOTED span of the source."""
    out: list = []
    try:
        for span in _quoted_spans(grounding or ""):
            pattern = _template_regex(span)
            if pattern is None:
                continue
            out.append(pattern)
            if len(out) >= _MAX_TEMPLATES:
                break
    except Exception:
        logger.exception("_template_patterns failed - grounding without templates")
    return out
