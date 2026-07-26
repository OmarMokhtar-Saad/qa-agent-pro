"""AC-anchoring check (SHYJ-7154 Fix 3).

When a source ticket carries REAL, human-authored acceptance criteria, every
generated test case should trace back to one of them. Cases that cite a
non-existent AC id (hallucinated traceability) or cite none at all are
"unanchored". This module flags them (advisory) and can optionally drop the
hallucinated ones (QA_AC_ANCHORING_ENFORCE), never emptying the suite.

Only meaningful when the ACs were parsed from the SOURCE content — synthesized
ACs are not a ground-truth anchor, so the caller passes only source ACs here.

Never raises — every helper degrades to a benign result so it can never break
generation.
"""

from __future__ import annotations

import logging
import re

from tools.models import TestCase
from tools.rtm import AcceptanceCriterion, normalize_ac_id

logger = logging.getLogger(__name__)


def _real_ac_ids(source_acs: list[AcceptanceCriterion]) -> set[str]:
    return {normalize_ac_id(ac.ac_id) for ac in source_acs if ac and ac.ac_id}


def classify_cases(
    cases: list[TestCase], source_acs: list[AcceptanceCriterion]
) -> dict:
    """Split cases into anchored / hallucinated / unanchored by requirement_id.

    - anchored: requirement_id maps to a REAL source AC id.
    - hallucinated: requirement_id is set but maps to NO real AC id.
    - unanchored: requirement_id is empty/None.

    Returns a dict of tc_id lists. Never raises.
    """
    real = _real_ac_ids(source_acs)
    anchored: list[str] = []
    hallucinated: list[str] = []
    unanchored: list[str] = []
    try:
        for tc in cases:
            rid = normalize_ac_id(getattr(tc, "requirement_id", "") or "")
            if not rid:
                unanchored.append(tc.tc_id)
            elif rid in real:
                anchored.append(tc.tc_id)
            else:
                hallucinated.append(tc.tc_id)
    except Exception:
        logger.exception("classify_cases failed — treating all as anchored")
        return {
            "anchored": [tc.tc_id for tc in cases],
            "hallucinated": [],
            "unanchored": [],
        }
    return {
        "anchored": anchored,
        "hallucinated": hallucinated,
        "unanchored": unanchored,
    }


def filter_unanchored_cases(
    cases: list[TestCase], source_acs: list[AcceptanceCriterion]
) -> list[TestCase]:
    """Drop cases that cite a NON-EXISTENT AC id (hallucinated traceability).

    Only hallucinated cases are dropped — a null requirement_id is KEPT (the
    model simply didn't tag it, which is legitimate). Never empties the suite:
    if dropping would remove every case, the input is returned unchanged. Never
    raises.
    """
    try:
        if not source_acs:
            return cases
        real = _real_ac_ids(source_acs)
        kept: list[TestCase] = []
        for tc in cases:
            rid = normalize_ac_id(getattr(tc, "requirement_id", "") or "")
            if rid and rid not in real:
                continue  # hallucinated AC id — drop
            kept.append(tc)
        if not kept:
            logger.warning("AC anchoring would empty the suite — keeping all cases")
            return cases
        dropped = len(cases) - len(kept)
        if dropped:
            logger.info("AC anchoring dropped %d hallucinated-AC case(s)", dropped)
        return kept
    except Exception:
        logger.exception("filter_unanchored_cases failed — keeping all cases")
        return cases


def anchoring_warning_section(
    cases: list[TestCase], source_acs: list[AcceptanceCriterion]
) -> str:
    """Advisory markdown flagging cases not traceable to a REAL source AC.

    Returns "" when there are no source ACs (nothing to anchor to) or when every
    case is anchored. Never raises.
    """
    try:
        if not source_acs or not cases:
            return ""
        buckets = classify_cases(cases, source_acs)
        hallucinated = buckets["hallucinated"]
        unanchored = buckets["unanchored"]
        if not hallucinated and not unanchored:
            return ""
        lines = [
            "\n\n## AC Anchoring (advisory)",
            "",
            "These test cases are not traceable to any of the "
            f"{len(source_acs)} acceptance criteria in the source ticket. "
            "Review them — they may test behaviour the ticket does not specify:",
        ]
        if hallucinated:
            lines.append(
                "- **Cite a non-existent AC id (hallucinated):** "
                + ", ".join(hallucinated[:20])
                + (" …" if len(hallucinated) > 20 else "")
            )
        if unanchored:
            lines.append(
                "- **No acceptance criterion cited:** "
                + ", ".join(unanchored[:20])
                + (" …" if len(unanchored) > 20 else "")
            )
        return "\n".join(lines)
    except Exception:
        logger.exception("anchoring_warning_section failed — returning empty string")
        return ""


# --- Sub-task scope flagging (parent-story background) ----------------------
#
# When a Jira SUB-TASK is the target, its parent story is injected as BACKGROUND
# so the model understands the surrounding behaviour. The risk is scope drift:
# cases that test the PARENT instead of the sub-task. These helpers surface that
# drift next to the AC-anchoring advisory above. They FLAG ONLY — no case is ever
# dropped, reordered or mutated, so they are safe to run before the AC filter.

_WORD_RE = re.compile(r"[a-z0-9]+")

# Deliberately small and generic: test-boilerplate and grammar words that carry
# no scope signal in either direction.
_SCOPE_STOPWORDS = frozenset(
    {
        "that",
        "this",
        "with",
        "from",
        "into",
        "have",
        "will",
        "must",
        "when",
        "then",
        "than",
        "them",
        "they",
        "there",
        "these",
        "those",
        "should",
        "verify",
        "check",
        "ensure",
        "confirm",
        "test",
        "case",
        "user",
        "users",
        "given",
        "while",
        "after",
        "before",
        "each",
        "also",
        "only",
        "does",
        "been",
        "being",
        "using",
        "used",
        "which",
        "where",
        "what",
        "some",
        "such",
        "same",
        "other",
        "value",
        "values",
        "shown",
        "correct",
        "valid",
        "invalid",
        "system",
        "screen",
        "page",
    }
)

# If the heuristic would flag more than this share of the suite it is reacting to
# a vocabulary mismatch, not to real scope drift — so it suppresses itself rather
# than spamming the tester with a useless wall of tc_ids.
_MAX_FLAG_RATIO = 0.5


def _terms(text: str) -> set[str]:
    """Lower-cased content words (>= 4 chars, non-stopword) of *text*.

    Crude plural folding (trailing "s" on words longer than 4 chars) so
    "refunds" and "refund" match. Never raises.
    """
    try:
        out: set[str] = set()
        for raw in _WORD_RE.findall((text or "").lower()):
            if len(raw) < 4 or raw in _SCOPE_STOPWORDS:
                continue
            out.add(raw[:-1] if len(raw) > 4 and raw.endswith("s") else raw)
        return out
    except Exception:
        logger.exception("_terms failed — treating the text as empty")
        return set()


def _case_terms(tc: TestCase) -> set[str]:
    """Content words of a case's title + step actions + expected results."""
    chunks = [getattr(tc, "title", "") or ""]
    for step in getattr(tc, "steps", None) or []:
        chunks.append(getattr(step, "action", "") or "")
        chunks.append(getattr(step, "expected_result", "") or "")
    return _terms(" ".join(chunks))


def flag_out_of_scope_cases(
    cases: list[TestCase], target_text: str, parent_context: str
) -> set[str]:
    """stable_ids of cases that look like they cover the PARENT background rather
    than the target described in `## Feature to Test`.

    Deliberately conservative: a case is flagged only when it shares NO content
    word with the target AND does share one with parent-ONLY material. Returns
    an empty set when that would flag more than _MAX_FLAG_RATIO of the suite.

    FLAGS ONLY — never drops, reorders or mutates a case. Never raises; any
    failure yields an empty set (nothing flagged).
    """
    try:
        if not cases or not target_text or not parent_context:
            return set()
        target = _terms(target_text)
        if not target:
            return set()
        parent_only = _terms(parent_context) - target
        if not parent_only:
            return set()
        flagged: set[str] = set()
        for tc in cases:
            terms = _case_terms(tc)
            if not terms or (terms & target):
                continue
            if terms & parent_only:
                sid = getattr(tc, "stable_id", "") or ""
                if sid:
                    flagged.add(sid)
        if len(flagged) > len(cases) * _MAX_FLAG_RATIO:
            logger.info(
                "Scope heuristic flagged %d/%d cases — suppressing as a vocabulary "
                "mismatch rather than real scope drift",
                len(flagged),
                len(cases),
            )
            return set()
        return flagged
    except Exception:
        logger.exception("flag_out_of_scope_cases failed — flagging nothing")
        return set()


def scope_warning_section(cases: list[TestCase], flagged_stable_ids: set[str]) -> str:
    """Advisory markdown naming the flagged cases by their FINAL tc_id.

    Returns "" when nothing was flagged. Never raises.
    """
    try:
        if not cases or not flagged_stable_ids:
            return ""
        tc_ids = [
            tc.tc_id
            for tc in cases
            if (getattr(tc, "stable_id", "") or "") in flagged_stable_ids
        ]
        if not tc_ids:
            return ""
        return "\n".join(
            [
                "\n\n## Scope Check (advisory)",
                "",
                "These test cases read as covering the PARENT story's background "
                "rather than the specific ticket you asked about. Nothing was "
                "removed — review them and drop any that belong to a different "
                "piece of work:",
                "- " + ", ".join(tc_ids[:20]) + (" …" if len(tc_ids) > 20 else ""),
            ]
        )
    except Exception:
        logger.exception("scope_warning_section failed — returning empty string")
        return ""
