"""The lexical title-token prescreen that finds candidate duplicate pairs."""

from __future__ import annotations

import re

from agents.host_dedup_consts import _UNTRUSTED_ERRORS
from agents.host_dedup_contrast import _is_boundary_contrast, quoted_literals_differ

# --- Server-assisted duplicate shortlist (QA_DUP_SHORTLIST_ENABLED, OFF) -- #
# Lexical PRESCREEN over the merged, globally renumbered cases. Pairs are
# reported with POST-MERGE GLOBAL tc_ids (the phase-1 review settled on global
# ids to dodge the per-category TC-001 collision trap) so the host CONFIRMS a
# shortlist via the finalize sidecar instead of re-reading the merged suite.
# ADVISORY only: nothing is removed here, and a confirmed sidecar still passes
# through _extract_duplicate_groups + screen_duplicate_groups unchanged.
#
# F08 (2026-08-19) -- the feature CHANGED, and the reason is measured rather
# than asserted. This used to score `title + first step action` with
# difflib.SequenceMatcher.ratio() at >= 0.75, the same machinery as the
# advisory agreement label below. Replayed against the 2026-08-16 live run's
# 96 persisted cases that emitted 21 pairs -- truncated to the 12-pair cap, so
# the list was SATURATED and hiding nine more -- of which exactly one was a
# real near-duplicate. Cause: in a generated suite the shared scaffolding
# ("per-transaction limit sar ...", "disable ... channel on ... card") is most
# of the character mass, while the discriminating content is a numeral or a
# direction word, so `SAR 1,500 accepted` vs `SAR 1,050 rejected` scored 0.906
# -- ABOVE the one pair worth surfacing at 0.827. No threshold fixes that: any
# cut keeping the real pair keeps seven false positives with it. Suppressing
# the tier outright would also stop surfacing the real pair, so it was
# rejected too.
#
# Jaccard over TITLE tokens instead: each discriminating word counts once
# rather than in proportion to its length, and dropping the action removes the
# most templated field in the suite. Same run, same cases: 21 pairs -> 3, the
# real pair retained, precision 1/12 -> 1/3, and the cap no longer binds. A
# numeric-disagreement veto was measured too (21 -> 1) and REJECTED: the pair
# it drops is the real one.
DUP_SHORTLIST_MIN_RATIO = 0.65
_DUP_SHORTLIST_MAX_PAIRS = 12
_DUP_SHORTLIST_MAX_CASES = 200
_DUP_SHORTLIST_TITLE_CHARS = 80
# Title chars scanned for tokens (the rendered title is capped separately at
# _DUP_SHORTLIST_TITLE_CHARS) and the cheap size prefilter: two token sets
# whose sizes differ by more than this factor cannot reach the threshold.
_DUP_SHORTLIST_TITLE_SCAN = 300
# Word tokens, keeping a hyphenated term ("per-transaction") and a formatted
# number ("5,000", "1.5") whole -- splitting those was measured to drop the
# one pair on the F08 run worth surfacing from 0.667 to 0.615, under the
# threshold. Unicode-aware, so an Arabic title tokenises too.
_DUP_SHORTLIST_TOKEN_RE = re.compile(r"[^\W_](?:[^\W_]|[-,.](?=[^\W_]))*", re.UNICODE)


def dup_shortlist_on() -> bool:
    """The server-assisted duplicate shortlist is unconditional since
    2026-08-12 (QA_DUP_SHORTLIST_ENABLED was deleted; it had soaked ON since
    2026-08-04)."""
    return True


def dup_title_tokens(case: object) -> frozenset:
    """Lower-cased word tokens of a JSON-native merged-case dict's TITLE (the
    shape ``mcp_handlers.merge_category_rows`` emits), for the Jaccard
    prescreen. The first step's action is deliberately NOT included -- it is the
    most templated field in a generated suite and was the main driver of the F08
    false positives. UNTRUSTED input; never raises, returns an empty set on
    anything unusable."""
    try:
        if not isinstance(case, dict):
            return frozenset()
        title = str(case.get("title") or "")[:_DUP_SHORTLIST_TITLE_SCAN]
        return frozenset(_DUP_SHORTLIST_TOKEN_RE.findall(title.lower()))
    except _UNTRUSTED_ERRORS:
        return frozenset()


def _shortlist_entries(merged_cases: list) -> list:
    """``(tc_id, title, title_tokens)`` for each usable dict case, capped."""
    entries = []
    for c in (merged_cases or [])[:_DUP_SHORTLIST_MAX_CASES]:
        if not isinstance(c, dict):
            continue
        tid = str(c.get("tc_id") or "")
        toks = dup_title_tokens(c)
        if tid and toks:
            entries.append((tid, str(c.get("title") or ""), toks))
    return entries


def _pair_ratio(a: frozenset, b: frozenset) -> float:
    """Jaccard of two token sets, or 0.0 when the pair is not a candidate."""
    # |a & b| <= min(|a|, |b|) and |a | b| >= max(|a|, |b|), so this
    # bounds the Jaccard from above without building either set.
    if min(len(a), len(b)) < DUP_SHORTLIST_MIN_RATIO * max(len(a), len(b)):
        return 0.0
    union = len(a | b)
    if not union:
        return 0.0
    ratio = len(a & b) / union
    if ratio < DUP_SHORTLIST_MIN_RATIO:
        return 0.0
    # F5: a deliberate contrast pair is not a duplicate candidate.
    if _is_boundary_contrast(a, b):
        return 0.0
    return ratio


def _shortlist_pairs(entries: list) -> list:
    """Every candidate pair row over ``entries`` (unsorted, uncapped)."""
    pairs: list = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            ratio = _pair_ratio(entries[i][2], entries[j][2])
            if not ratio or quoted_literals_differ(entries[i][1], entries[j][1]):
                continue
            pairs.append(
                {
                    "id_a": entries[i][0],
                    "title_a": entries[i][1],
                    "id_b": entries[j][0],
                    "title_b": entries[j][1],
                    "ratio": round(ratio, 3),
                }
            )
    return pairs
