"""Provenance source tags: the allowlist and its normaliser."""

from __future__ import annotations

import logging
import re

from config.settings_guard import never_raise

logger = logging.getLogger("tools.atomic_checklist")


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

# ALLOWLIST of source tags, and its HONEST limits. ``source`` is SELF-REPORTED
# by the decomposition model, so it is a HINT, not a control: a sentence the
# model labels "description" is indistinguishable here from a real description
# sentence. What the allowlist DOES buy is that no attacker-chosen STRING can be
# rendered as authority — the optional ``:<ref>`` suffix must look like a
# criterion/section IDENTIFIER (AC-003, AF03, BR12, REQ-7), never free text, so
# BOTH "description:APPROVED BY SECURITY" and "description:APPROVED-BY-SECURITY"
# fold to "unattributed" (which also drags the granularity audit's provenance
# ratio down). PROVENANCE_LIMITATION states the residual gap in every report.
_SOURCE_REF = r"(?::[A-Za-z]{1,6}[-_.]?\d{1,4}(?:\.\d{1,3})?)?"
_SOURCE_PATTERNS = (
    re.compile(rf"^acceptance_criteria{_SOURCE_REF}$", re.IGNORECASE),
    # F9 (2026-08-30): the bare label form a host actually writes when the job
    # asks it to attribute a decomposed requirement -- "AC1", "AC-10", "AC 03".
    # It was not on the allowlist, so EVERY correctly-attributed item folded to
    # "unattributed" and the workbook's Source column rendered one constant.
    # This admits an IDENTIFIER shape, never free text, so the control the
    # allowlist exists to be is unchanged: "TICKET-10974 Proposed Change" still
    # folds, and so does "AC1: APPROVED BY SECURITY".
    # 2026-08-31 (F10): this was AC-ONLY, so `BR01` and `MSG01` -- the labels a
    # Business Rules ticket actually uses, and two of the very shapes the
    # comment above names as acceptable ("AC-003, AF03, BR12, REQ-7") -- folded
    # to "unattributed", and the workbook's Source column rendered one constant
    # across all seventeen items. Still an IDENTIFIER shape only, never free
    # text, so the control the allowlist exists to be is unchanged.
    re.compile(r"^[A-Z]{2,5}[-_ ]?\d{1,4}$", re.IGNORECASE),
    re.compile(rf"^description{_SOURCE_REF}$", re.IGNORECASE),
    re.compile(r"^comment(#\d{1,4})?$", re.IGNORECASE),
    re.compile(r"^amendment(#\d{1,4})?$", re.IGNORECASE),
    re.compile(r"^parent_story$", re.IGNORECASE),
    re.compile(r"^implied$", re.IGNORECASE),
)

# Which normalised sources point at ticket COMMENT text — text any Jira user with
# ticket access can write. Pass 1 is not given the comment thread (Batch 1 owns
# it), so an item can only land in this bucket when the model attributes
# something in the description or the parent text to a comment. When it does,
# every renderer says so — but see PROVENANCE_LIMITATION: the ABSENCE of this tag
# proves nothing.
_COMMENT_SOURCE_RE = re.compile(r"^(comment(#\d{1,4})?|amendment(#\d{1,4})?)$")

UNATTRIBUTED = "unattributed"

# Printed in EVERY rendered provenance block, unconditionally. The previous
# revision of this feature asserted that a comment#N tag "survives" as though it
# were a control; it is not one, and a report that implies otherwise is worse
# than a report carrying no tags at all.
PROVENANCE_LIMITATION = (
    "Provenance tags are SELF-REPORTED by the decomposition model and are "
    "validated only against a shape allowlist — they are a reading aid, NOT a "
    "security control. A requirement tagged `description` is not PROVEN to come "
    "from the description, and an unflagged requirement is not proven to be "
    "free of comment-thread influence. Confirm anything you cannot find in the "
    "approved specification."
)


# Fold a model-reported provenance tag onto the allowlist. Never raises.
#
# Matching is case-insensitive but the tag is returned as written (whitespace
# normalised, length capped) so a ref like "acceptance_criteria:AC-003" keeps
# its readable casing. Returns ``"unattributed"`` for anything unrecognised,
# including an injected authority claim.
def normalize_source(raw: str) -> str:
    """Fold a model-reported provenance tag onto the allowlist. Never raises."""
    with never_raise(logger, "normalize_source failed", exc_info=True):
        tag = " ".join(str(raw or "").strip().split())[:60]
        if not tag:
            return UNATTRIBUTED
        for pattern in _SOURCE_PATTERNS:
            if pattern.match(tag):
                # F9: canonicalise the bare label onto the qualified form the
                # rest of the report already speaks, so "AC1" and
                # "acceptance_criteria:AC-001" do not read as two sources.
                bare = re.match(r"^AC[-_ ]?(\d{1,4})$", tag, re.IGNORECASE)
                if bare:
                    return f"acceptance_criteria:AC-{int(bare.group(1)):03d}"
                return tag
        return UNATTRIBUTED
    return UNATTRIBUTED


def is_comment_derived(source: str) -> bool:
    """True when a normalised source tag points at ticket comment text."""
    with never_raise():
        return bool(_COMMENT_SOURCE_RE.match(str(source or "").strip().lower()))
    return False
