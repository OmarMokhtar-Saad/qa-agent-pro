"""Constants and the logger of the host duplicate-review pass (see ``agents.host_dedup``)."""

from __future__ import annotations

import logging
import re

# One logger for the whole split, named after the facade so log filters keep working.
logger = logging.getLogger("agents.host_dedup")


# --------------------------------------------------------------------------- #
# Piece 1: host-reviewed duplicate review (QA_HOST_DEDUP_REVIEW_ENABLED)
#
# The server itself collapses only byte-identical duplicates. The
# meaning engine used instead is the tester's OWN chat model, which
# is ALREADY in the loop and already holds the merged 8-category set: prepare asks
# it to review that set and return an optional top-level ``duplicate_groups``, and
# submit acts on it deterministically here in Python. No extra round trip (the
# field rides the existing submission), no server-side LLM call, no API key.
#
# DEFAULT IS FLAG-ONLY. Nothing is removed unless QA_HOST_DEDUP_APPLY is ALSO on.
#
# THREE LAYERS, in this order, because the field is UNTRUSTED INPUT -- the threat is
# not "an untrustworthy host model" but injected content inside the _GUARD-wrapped
# Jira/comment text that this design deliberately places in the host's context:
#
#   1. _extract_duplicate_groups -- SHAPE validation. json-native data only, no
#      eval, every id checked against the submitted suite, group/size/note caps,
#      and an overlap rule so groups cannot CHAIN. This is NOT a safety bound: it
#      permits 50 x 12 = 550 removable ids, i.e. a DISJOINT PARTITION of the suite.
#   2. screen_duplicate_groups -- the DETERMINISTIC SAFETY SCREEN on the apply path:
#      no cluster larger than 4 cases, and a proportional cap on the total share of
#      the suite one review may remove (refused WHOLESALE above it). Both bounds are
#      corpus-independent, so they are guarantees rather than tuned guesses; a
#      lexical similarity floor was measured and REJECTED as a gate (dup_agreements
#      carries the numbers) and ships as an advisory label instead.
#   3. apply_duplicate_groups -- removal over ALREADY-SCREENED groups only, with
#      the NB-016 sole-requirement-tracer rescue mirrored from the agent.
#
# Every layer is pure, synchronous, stdlib-only and never raises.
# --------------------------------------------------------------------------- #

# Hard caps on the untrusted field's SHAPE. settings may lower these, never raise.
_DUP_MAX_GROUPS = 50
_DUP_MAX_GROUP_SIZE = 12
# Cap on the validation/refusal notes echoed back, so a hostile field cannot turn
# the reply into a thousand-line rejection log.
_MAX_DUP_NOTES = 20
# The reply section is bounded by CHARACTERS, not by group count: a group-count cap
# still allowed a ~36 KB section, and truncating by groups degraded disclosure to an
# aggregate count in exactly the mass-removal case. Truncation now never hides a
# deletion -- build_duplicate_section lists every removed id when it truncates.
_MAX_DUP_SECTION_CHARS = 3500
_MAX_DUP_REMOVED_IDS = 100

# The two bounds on REMOVAL, both corpus-independent so neither needs calibration.
# _DUP_MAX_APPLY_GROUP_SIZE has NO .env knob on purpose: it is derived from the
# design (8 categories, one BEHAVIOUR per test => a genuine cross-category duplicate
# cluster is 2 cases, occasionally 3), not from a corpus, so there is nothing for an
# operator to tune and nothing to weaken.
_DUP_MAX_APPLY_GROUP_SIZE = 4
_DUP_REMOVAL_RATIO_CEILING = 0.40
_DUP_REMOVAL_RATIO_DEFAULT = 0.35
# Presentation only -- the threshold below which a group is LABELLED low-agreement.
_DUP_LOW_TEXT_DEFAULT = 0.50

# Priority rank used to pick a group's keeper. risk_score is deliberately NOT used:
# risk is scored later, inside _finalize_generation, so every case still scores 0
# at this point.
_DUP_PRIORITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}

# Non-alphanumeric runs collapse to one space before the lexical comparison.
_DUP_WS_RE = re.compile(r"[^a-z0-9]+")


# What reading untrusted host-authored shapes can raise; fail-soft helpers catch
# exactly these instead of a blind ``Exception``.
_UNTRUSTED_ERRORS = (
    AttributeError,
    TypeError,
    ValueError,
    IndexError,
    KeyError,
    OverflowError,
    RecursionError,
)


# Validation / refusal notes echoed back to the tester (``str.format`` templates and plain text).
_NOT_A_LIST_NOTE = (
    "`duplicate_groups` was not a list of groups -- the whole field was "
    "ignored (no case was removed or reported as a duplicate)."
)
_UNREADABLE_NOTE = (
    "`duplicate_groups` could not be read -- it was ignored (no case was removed)."
)
_UNSCREENED_NOTE = (
    "`duplicate_groups` could not be screened -- no group was honoured and "
    "nothing was removed."
)
_NOT_LIST_ENTRY_NOTE = "a `duplicate_groups` entry was not a list of tc_ids -- skipped."
_TOO_FEW_NOTE = (
    "a duplicate group named fewer than two distinct known cases -- skipped."
)
_NONSTR_NOTE = "a non-string tc_id in `duplicate_groups` was ignored."
_UNKNOWN_NOTE = "`{}` is not a tc_id in the submitted suite -- ignored."
_CLAIMED_NOTE = (
    "`{}` was already in an earlier duplicate group -- ignored in the later one."
)
_OVERSIZE_NOTE = (
    "a duplicate group named more than {} cases -- the extra ids were ignored."
)
_TOO_MANY_NOTE = (
    "`duplicate_groups` named {} groups -- only the first {} were considered."
)
