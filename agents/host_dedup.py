"""Host-mode duplicate detection: the shortlist prescreen and the host-reviewed
``duplicate_groups`` validation, screen, apply and reply section.

Moved verbatim out of ``agents/host_mode.py`` (clean-code audit part 05);
``host_mode`` re-exports every public name, so existing callers are unchanged.
"""

from __future__ import annotations

import difflib
import logging
import re

from config.settings import settings
from tools.rtm import normalize_ac_id

logger = logging.getLogger(__name__)

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


def _dup_text(tc) -> str:
    """Normalised comparison text for the ADVISORY agreement label: title + FIRST
    step action, lower-cased with every non-alphanumeric run collapsed to one space.

    Deliberately mirrors ``agents.test_scenario_agent._semantic_payload``'s choice of
    fields, so the reported number describes the same content that helper
    compares. Never raises.
    """
    try:
        title = getattr(tc, "title", "") or ""
        steps = getattr(tc, "steps", None) or []
        action = (getattr(steps[0], "action", "") or "") if steps else ""
        return _DUP_WS_RE.sub(" ", f"{title} {action}".lower()).strip()[:600]
    except Exception:
        return ""


def _dup_text_ratio(a, b) -> float:
    """Server-measured textual agreement in [0, 1] between two cases (stdlib
    ``difflib`` only -- no optional dependency, no network, no LLM, no
    async, no I/O). Never raises.

    ADVISORY ONLY. It is REPORTED, never used to veto or to authorise a removal --
    see ``dup_agreements`` for the measurements that forbid gating on it.
    """
    try:
        ta, tb = _dup_text(a), _dup_text(b)
        if not ta or not tb:
            return 0.0
        return difflib.SequenceMatcher(None, ta, tb).ratio()
    except Exception:
        return 0.0


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
_DUP_SHORTLIST_MIN_RATIO = 0.65
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


def _dup_title_tokens(case: object) -> frozenset:
    """Lower-cased word tokens of a JSON-native merged-case dict's TITLE (the
    shape ``mcp_handlers._merge_category_rows`` emits), for the Jaccard
    prescreen. The first step's action is deliberately NOT included -- it is the
    most templated field in a generated suite and was the main driver of the F08
    false positives. UNTRUSTED input; never raises, returns an empty set on
    anything unusable."""
    try:
        if not isinstance(case, dict):
            return frozenset()
        title = str(case.get("title") or "")[:_DUP_SHORTLIST_TITLE_SCAN]
        return frozenset(_DUP_SHORTLIST_TOKEN_RE.findall(title.lower()))
    except Exception:
        return frozenset()


def _shortlist_safe(text: object, cap: int) -> str:
    """Sanitise UNTRUSTED host text for interpolation into the reply: strip
    backticks and newlines (backtick-span breakout) and cap the length."""
    try:
        return str(text or "").replace("`", "").replace("\n", " ").strip()[:cap]
    except Exception:  # pragma: no cover
        return ""


# F5 (2026-08-30, MEASURED): across three live runs the prescreen surfaced 8
# candidate pairs and NONE was a duplicate -- every one was a deliberate
# boundary or contrast pair (min vs max, below vs above, increased vs
# decreased, production vs UAT, negative vs non-numeric). That is not bad luck:
# title-word overlap is HIGHEST precisely where two cases are opposites, which
# is the coverage a good suite must contain. The finalize reply then told the
# tester their (correct) clean review was "CONTRADICTED by the server's own
# prescreen", nudging a non-technical tester toward deleting boundary coverage.
#
# The filter is the one the section's OWN guidance already states -- "drop any
# pair that differs in boundary value" -- applied server-side instead of being
# delegated to the reader. A pair is suppressed only when EVERY token the two
# titles disagree about is a discriminator: a contrast word, or a token
# carrying a digit. Two cases that differ in a product noun are untouched.
_DUP_DISCRIMINATORS = frozenset(
    {
        "min",
        "max",
        "minimum",
        "maximum",
        "lower",
        "upper",
        "least",
        "most",
        "above",
        "below",
        "over",
        "under",
        "before",
        "after",
        "beyond",
        "within",
        "increase",
        "increased",
        "increases",
        "decrease",
        "decreased",
        "decreases",
        "more",
        "less",
        "fewer",
        "greater",
        "smaller",
        "larger",
        "longer",
        "shorter",
        "first",
        "last",
        "start",
        "end",
        "top",
        "bottom",
        "valid",
        "invalid",
        "enabled",
        "disabled",
        "allowed",
        "blocked",
        "present",
        "absent",
        "empty",
        "full",
        "positive",
        "negative",
        "numeric",
        "alphanumeric",
        "alphabetic",
        "cyrillic",
        "unicode",
        "ascii",
        "single",
        "multiple",
        "none",
        "all",
        "missing",
        "extra",
        "production",
        "prod",
        "staging",
        "uat",
        "sandbox",
        "qa",
        "dev",
        "development",
        "preprod",
        "live",
        "local",
        "penny",
        "cent",
        "zero",
        "one",
        "two",
        "non",
        "not",
        "no",
        "android",
        "ios",
        "web",
        "mobile",
        "desktop",
        "tablet",
    }
)


def _is_boundary_contrast(tokens_a: frozenset, tokens_b: frozenset) -> bool:
    """True when two titles disagree ONLY about contrast WORDS.

    Pure and never raises. Returns False when the titles are identical -- an
    identical pair is the one shape that really IS a duplicate, and suppressing
    it would break the check this filter exists to make usable.

    A DIGIT-BEARING token is deliberately NOT a discriminator, though the first
    cut of this filter treated it as one. "penny below minimum" ~ "penny above
    minimum" is a boundary pair because of below/above, not because of a
    number; meanwhile 40 titles differing only by "variant 1", "variant 2", ...
    are the duplicate shape this prescreen exists to catch, and the digit rule
    silenced them (tests/test_dup_prescreen_merged_submit.py pins that fixture).
    Re-measured against the eight pairs the 2026-08-30 run reported: the word
    list alone suppresses seven of the eight.
    """
    try:
        diff = tokens_a.symmetric_difference(tokens_b)
        if not diff:
            return False
        return all(t in _DUP_DISCRIMINATORS for t in diff)
    except Exception:  # pragma: no cover - a filter never breaks the prescreen
        return False


def _shortlist_entries(merged_cases: list) -> list:
    """``(tc_id, title, title_tokens)`` for each usable dict case, capped."""
    entries = []
    for c in (merged_cases or [])[:_DUP_SHORTLIST_MAX_CASES]:
        if not isinstance(c, dict):
            continue
        tid = str(c.get("tc_id") or "")
        toks = _dup_title_tokens(c)
        if tid and toks:
            entries.append((tid, str(c.get("title") or ""), toks))
    return entries


def _pair_ratio(a: frozenset, b: frozenset) -> float:
    """Jaccard of two token sets, or 0.0 when the pair is not a candidate."""
    # |a & b| <= min(|a|, |b|) and |a | b| >= max(|a|, |b|), so this
    # bounds the Jaccard from above without building either set.
    if min(len(a), len(b)) < _DUP_SHORTLIST_MIN_RATIO * max(len(a), len(b)):
        return 0.0
    union = len(a | b)
    if not union:
        return 0.0
    ratio = len(a & b) / union
    if ratio < _DUP_SHORTLIST_MIN_RATIO:
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
            if not ratio:
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


def build_dup_shortlist_counted(merged_cases: list) -> tuple[list, int]:
    """Candidate duplicate PAIRS over the merged, renumbered cases, WITH
    the uncapped total.

    Returns ``(pairs, total)``: *pairs* truncated to
    _DUP_SHORTLIST_MAX_PAIRS, *total* how many cleared the threshold BEFORE
    that truncation. D4 (2026-08-21) split this out of
    ``build_dup_shortlist``, which is now a thin wrapper carrying its exact
    previous contract, so a caller that RENDERS the list can NAME the
    shortfall when the cap binds instead of quietly showing 12 of N -- this
    repo's no-silent-caps rule, and not a hypothetical: the F08 replay
    recorded above was itself misread once because a saturated 12-pair list
    was hiding nine more. NOTHING about the similarity measure changed --
    there is still exactly ONE implementation and F08's constants are
    untouched, because the F08 note above rejected threshold-tuning on
    measurement and the D4 replay reproduced that result on a second suite
    (see .claude/plans/plan-d4-d5-TICKET5646-2026-08-21.md for the sweep).

    The original contract, unchanged:

    Pure, synchronous, stdlib only -- no LLM, no I/O.
    Deterministic and bounded: at most _DUP_SHORTLIST_MAX_CASES cases are
    compared, a size prefilter skips cheap non-matches, and at most
    _DUP_SHORTLIST_MAX_PAIRS pairs are returned, highest agreement first.
    UNTRUSTED input tolerated (host-authored dicts); never raises, returns []
    on anything unusable. Output rows: {id_a, title_a, id_b, title_b, ratio}
    where the ids are POST-MERGE GLOBAL tc_ids and ``ratio`` is now the Jaccard
    overlap of the two TITLE token sets -- see the F08 note on the constants
    above for why the character-level difflib score was dropped.
    """
    try:
        pairs = _shortlist_pairs(_shortlist_entries(merged_cases))
        pairs.sort(key=lambda p: (-p["ratio"], p["id_a"], p["id_b"]))
        return pairs[:_DUP_SHORTLIST_MAX_PAIRS], len(pairs)
    except Exception:
        logger.debug("build_dup_shortlist_counted failed", exc_info=True)
        return [], 0


def build_dup_shortlist(merged_cases: list) -> list:
    """The capped pair list only -- the pre-D4 signature and behaviour,
    byte-for-byte. Retained because ``mcp_handlers._dup_shortlist_note``
    (the qa_submit_category call site) and the F08 tests are written
    against it. See ``build_dup_shortlist_counted``."""
    return build_dup_shortlist_counted(merged_cases)[0]


def build_dup_shortlist_section(pairs: list) -> str:
    """Markdown appendix for the qa_submit_category reply that completed the
    expected set. "" when there are no pairs. Titles are sanitised (UNTRUSTED
    host text) and the ids shown are POST-MERGE GLOBAL tc_ids, which the
    finalize sidecar passes through unchanged. Never raises."""
    try:
        if not pairs:
            return ""
        lines = [
            "",
            "### \U0001f50d Candidate duplicate pairs "
            "(server lexical prescreen -- ADVISORY)",
            "",
            "Every expected category is staged, so the server compared the "
            "merged case TITLES lexically (shared-word overlap, stdlib only "
            "-- no LLM). These pairs look like the SAME test. The "
            "ids are POST-MERGE GLOBAL tc_ids: confirm a shortlist instead "
            "of re-reading the merged suite by finalizing with "
            '`suite_json={"duplicate_groups": [["<id>", "<id>"], ...]}` (no '
            "`test_cases`), keeping ONLY the pairs you agree are one test "
            "and using these ids exactly as printed. If you review these pairs "
            "and agree with NONE of them, finalize with "
            '`suite_json={"duplicate_groups": []}`: an EMPTY list records the '
            "review as RUN with none found, while omitting the field is "
            "recorded as NO REVIEW RAN. This is a lexical "
            "prescreen, not a verdict -- drop any pair that differs in "
            "boundary value, role, error message, or platform. By default "
            "nothing is removed (the review is advisory); every confirmed "
            "group is still screened server-side before any removal.",
            "",
        ]
        for p in pairs[:_DUP_SHORTLIST_MAX_PAIRS]:
            if not isinstance(p, dict):
                continue
            id_a = _shortlist_safe(p.get("id_a"), 16)
            id_b = _shortlist_safe(p.get("id_b"), 16)
            t_a = _shortlist_safe(p.get("title_a"), _DUP_SHORTLIST_TITLE_CHARS)
            t_b = _shortlist_safe(p.get("title_b"), _DUP_SHORTLIST_TITLE_CHARS)
            try:
                ratio = float(p.get("ratio") or 0.0)
            except (TypeError, ValueError, OverflowError):
                ratio = 0.0
            lines.append(
                f'- `{id_a}` "{t_a}" ~ `{id_b}` "{t_b}" (lexical agreement {ratio:.2f})'
            )
        lines.append("")
        return "\n".join(lines) + "\n"
    except Exception:
        logger.debug("build_dup_shortlist_section failed", exc_info=True)
        return ""


def dup_shortlist_cases_json(cases: list) -> list:
    """Adapt FINALIZED suite cases to the JSON-native shape the prescreen reads.

    D4 (2026-08-21) -- WHY THIS EXISTS. The prescreen was written for the
    per-category path, where ``mcp_handlers._merge_category_rows`` already hands
    it plain dicts. On the MERGED finalize path the cases are ``TestCase`` model
    objects, and they are read AFTER ``_finalize_generation`` -- deliberately,
    because that call renumbers every ``tc_id``, so these are the FINAL ids that
    match the exported workbook. Reading them any earlier would print ids that
    send the tester to the wrong rows.

    Accepts either shape. Pure, synchronous, stdlib only. Never raises; returns
    [] on anything unusable.
    """
    out: list = []
    try:
        for c in cases or []:
            if isinstance(c, dict):
                tid, title = c.get("tc_id"), c.get("title")
            else:
                tid, title = getattr(c, "tc_id", ""), getattr(c, "title", "")
            if tid:
                out.append({"tc_id": str(tid), "title": str(title or "")})
    except Exception:
        logger.debug("dup_shortlist_cases_json failed", exc_info=True)
    return out


def _headline_cap_note(n: int, total) -> str:
    """NO SILENT CAPS: when _DUP_SHORTLIST_MAX_PAIRS truncated the list, say so
    in the same breath as the count. A saturated list read as a complete one
    is exactly how the F08 replay was misread the first time."""
    try:
        if int(total) > n:
            return (
                f" {int(total)} pair(s) cleared the bar in all; only the "
                f"closest {n} are listed."
            )
    except (TypeError, ValueError, OverflowError):
        pass
    return ""


def build_dup_contradiction_headline(found: int, total: int) -> str:
    """The PROTECTED finalize-reply CLAIM: the host's empty duplicate review is
    contradicted by the server's own prescreen. "" when nothing was found.

    D4 (2026-08-21). On the TICKET-5646 run the host finalized through the merged
    route with ``duplicate_groups: []``, so the server recorded "review ran,
    none found" -- an assurance that was false over a suite in which a reviewer
    found nine redundant clusters. The prescreen that could have contradicted it
    already existed and was wired to ``qa_submit_category`` only, so it never
    ran on the route this suite took.

    Bounded by construction at 496 chars, which is what lets it afford to be a
    PROTECTED reply section. The EVIDENCE (the pair list) is a SEPARATE and
    TRIMMABLE section, ``build_dup_contradiction_pairs``: the claim about
    whether the deliverable is what it appears to be must survive the reply
    budget, the list backing it need not. See the reply-budget section of
    .claude/plans/plan-d4-d5-TICKET5646-2026-08-21.md for the measurements.

    HONESTY BOUND, stated here because it is the reason the wording is a FLOOR
    rather than a finding. Measured on the stored TICKET-5646 suite (96 cases,
    the run that produced this fix) against nine redundant clusters covering 33
    case pairs: this prescreen returns FIVE pairs and recovers exactly ONE
    cluster (TC-084 / TC-095, ratio 0.667). Four of the five are deliberate
    variants -- English/Arabic, Shipped/Delivered, Pending/Cancelled -- i.e.
    correctly distinct cases. Lowering _DUP_SHORTLIST_MIN_RATIO does NOT rescue
    it and was not done: 0.70 recovers none, 0.60 and 0.55 recover two of nine,
    and 0.30 recovers five of nine only by emitting forty-four pairs at 23%
    precision. That is the same shape F08 measured when it rejected
    threshold-tuning. So this text says "check these", never "remove these", and
    it says out loud that an EMPTY prescreen is not evidence either.

    Pure, synchronous, stdlib only. Never raises.
    """
    try:
        n = int(found)
    except (TypeError, ValueError, OverflowError):
        return ""
    if n <= 0:
        return ""
    more = _headline_cap_note(n, total)
    return (
        "> \u267b\ufe0f  **That duplicate review is CONTRADICTED by the "
        "server's own prescreen.** You reported no cross-category duplicates; "
        "a stdlib shared-word comparison of the FINAL case titles found "
        f"{n} candidate pair(s).{more} It is a FLOOR, not a measurement: "
        "replayed on a real 96-case suite in which a reviewer found nine "
        "redundant clusters it recovered ONE, so an empty prescreen is not "
        "evidence of a clean suite either. Nothing was removed.\n\n"
    )


def build_dup_contradiction_pairs(pairs: list) -> str:
    """The TRIMMABLE finalize-reply EVIDENCE behind the headline above: the
    candidate pairs themselves. "" when there are none.

    Deliberately NOT ``build_dup_shortlist_section``. That renderer's body is a
    call to action for a host still mid-flight ("confirm a shortlist via the
    finalize sidecar", "finalize with duplicate_groups: []"), which on a
    finalize reply is unfollowable -- the suite is validated, exported and
    persisted by the time this is read. Same measure, same sanitiser, different
    audience, and no dead instruction.

    Ids and titles are UNTRUSTED host text: every interpolation goes through
    ``_shortlist_safe`` (backtick-span breakout and newlines stripped, length
    capped). Pure, synchronous, stdlib only. Never raises.
    """
    try:
        rows = [p for p in (pairs or []) if isinstance(p, dict)]
        if not rows:
            return ""
        lines = [
            "",
            "### \U0001f50d Candidate duplicate pairs (server lexical "
            "prescreen -- ADVISORY)",
            "",
            "Titles only -- no LLM, nothing removed. Check each "
            "against the workbook and drop any that differ in boundary value, "
            "role, error message, language or platform; most near-identical "
            "titles turn out to be deliberate variants.",
            "",
        ]
        for p in rows[:_DUP_SHORTLIST_MAX_PAIRS]:
            try:
                ratio = float(p.get("ratio") or 0.0)
            except (TypeError, ValueError, OverflowError):
                ratio = 0.0
            id_a = _shortlist_safe(p.get("id_a"), 16)
            id_b = _shortlist_safe(p.get("id_b"), 16)
            t_a = _shortlist_safe(p.get("title_a"), _DUP_SHORTLIST_TITLE_CHARS)
            t_b = _shortlist_safe(p.get("title_b"), _DUP_SHORTLIST_TITLE_CHARS)
            lines.append(
                f'- `{id_a}` "{t_a}" ~ `{id_b}` "{t_b}" (lexical agreement {ratio:.2f})'
            )
        lines.append("")
        return "\n".join(lines) + "\n"
    except Exception:
        logger.debug("build_dup_contradiction_pairs failed", exc_info=True)
        return ""


def _dup_keeper_key(pair) -> tuple:
    """Sort key picking a group's keeper: highest declared priority first, ties
    broken by the earliest position in the submission. Deterministic and pure."""
    idx, tc = pair
    pri = getattr(getattr(tc, "priority", None), "value", "") or ""
    return (_DUP_PRIORITY_RANK.get(pri, 99), idx)


def _removal_ratio() -> float:
    """Max share of the SUBMITTED cases one host review may remove. An operator may
    LOWER this; the module CEILING wins, so it can never be raised."""
    try:
        cfg = float(
            getattr(
                settings, "qa_host_dedup_max_removal_ratio", _DUP_REMOVAL_RATIO_DEFAULT
            )
        )
    except (TypeError, ValueError, OverflowError):
        cfg = _DUP_REMOVAL_RATIO_DEFAULT
    return max(0.0, min(_DUP_REMOVAL_RATIO_CEILING, cfg))


def _low_text_ratio() -> float:
    """Threshold below which a group is LABELLED low-agreement in the report. Purely
    presentational -- it gates nothing (see ``dup_agreements``)."""
    try:
        cfg = float(
            getattr(settings, "qa_host_dedup_low_text_ratio", _DUP_LOW_TEXT_DEFAULT)
        )
    except (TypeError, ValueError, OverflowError):
        cfg = _DUP_LOW_TEXT_DEFAULT
    return max(0.0, min(1.0, cfg))


def _dup_group_caps() -> tuple[int, int]:
    """``(max_groups, max_size)`` from settings, clamped to the hard ceilings."""
    try:
        cfg_groups = int(
            getattr(settings, "qa_host_dedup_max_groups", _DUP_MAX_GROUPS)
            or _DUP_MAX_GROUPS
        )
    except (TypeError, ValueError, OverflowError):
        cfg_groups = _DUP_MAX_GROUPS
    try:
        cfg_size = int(
            getattr(settings, "qa_host_dedup_max_group_size", _DUP_MAX_GROUP_SIZE)
            or _DUP_MAX_GROUP_SIZE
        )
    except (TypeError, ValueError, OverflowError):
        cfg_size = _DUP_MAX_GROUP_SIZE
    return (
        min(_DUP_MAX_GROUPS, max(1, cfg_groups)),
        min(_DUP_MAX_GROUP_SIZE, max(2, cfg_size)),
    )


def _append_note(notes: list, msg: str) -> None:
    if len(notes) < _MAX_DUP_NOTES:
        notes.append(msg)


def _collect_group_members(
    entry: list, known: set, claimed: set, max_size: int, notes: list
) -> list:
    """The valid, unclaimed, de-duplicated tc_ids of one group entry (noting drops)."""
    members: list = []
    for m in entry:
        if not isinstance(m, str):
            _append_note(notes, "a non-string tc_id in `duplicate_groups` was ignored.")
            continue
        tid = m.strip()
        if tid not in known:
            # Strip backticks/newlines: this id is UNTRUSTED host text
            # interpolated inside a backtick span, and a crafted value
            # could otherwise break out of it.
            safe_tid = tid[:32].replace("`", "").replace("\n", " ")
            _append_note(
                notes,
                f"`{safe_tid}` is not a tc_id in the submitted suite -- ignored.",
            )
            continue
        if tid in members:
            # Self-reference / repeat inside one group: collapse silently.
            continue
        if tid in claimed:
            _append_note(
                notes,
                f"`{tid}` was already in an earlier duplicate group -- "
                "ignored in the later one.",
            )
            continue
        if len(members) >= max_size:
            _append_note(
                notes,
                f"a duplicate group named more than {max_size} cases -- the "
                "extra ids were ignored.",
            )
            break
        members.append(tid)
    return members


def _walk_duplicate_groups(raw: list, valid_ids, notes: list) -> list:
    """Shape-validate each entry of the ``duplicate_groups`` list, noting drops."""
    max_groups, max_size = _dup_group_caps()
    known = {str(i) for i in (valid_ids or ())}
    claimed: set = set()
    groups: list = []
    if len(raw) > max_groups:
        _append_note(
            notes,
            f"`duplicate_groups` named {len(raw)} groups -- only the first "
            f"{max_groups} were considered.",
        )
    for entry in raw[:max_groups]:
        if not isinstance(entry, list):
            _append_note(
                notes, "a `duplicate_groups` entry was not a list of tc_ids -- skipped."
            )
            continue
        members = _collect_group_members(entry, known, claimed, max_size, notes)
        if len(members) < 2:
            if members:
                _append_note(
                    notes,
                    "a duplicate group named fewer than two distinct known "
                    "cases -- skipped.",
                )
            continue
        claimed.update(members)
        groups.append(members)
    return groups


def _extract_duplicate_groups(raw, valid_ids) -> tuple[list, list]:
    """Validate the SHAPE of the UNTRUSTED top-level ``duplicate_groups`` field.

    Returns ``(groups, notes)``: groups is a list of lists of tc_ids that all EXIST
    in the submitted suite; notes explains every rejection so the reply can say what
    was ignored. NEVER raises and NEVER trusts the field -- an unreadable value
    degrades to "no dedup" plus a note.

    This is shape validation ONLY, and it is explicitly NOT a safety bound: the caps
    below permit ``50 x 12 = 550`` removable ids, and the overlap rule only stops
    groups from CHAINING, so nothing here prevents a DISJOINT PARTITION of the suite.
    ``screen_duplicate_groups`` carries the bounds that gate removal; both run before
    anything is deleted.

    Rules, all enforced here in Python over already-``json.loads``'d data (no eval,
    no ast, no dynamic attribute access):

      * absent / ``None``               -> ``([], [])`` -- the common case
      * not a list                      -> ``([], [note])``
      * a non-list group                -> skipped + noted
      * a non-str member                -> dropped + noted
      * an id not in the suite          -> dropped + noted (hallucinated / stale)
      * a repeated id inside one group (INCLUDING a self-reference) -> collapsed
      * an id already claimed by an EARLIER group -> dropped + noted, so
        overlapping groups cannot chain into "the whole suite is one duplicate"
      * fewer than 2 distinct known ids -> the group is a no-op, dropped + noted
      * beyond the group / group-size / note caps -> truncated + noted
    """
    notes: list = []
    try:
        if raw is None:
            return [], []
        if not isinstance(raw, list):
            return [], [
                "`duplicate_groups` was not a list of groups -- the whole field was "
                "ignored (no case was removed or reported as a duplicate)."
            ]
        groups = _walk_duplicate_groups(raw, valid_ids, notes)
        return groups, notes[:_MAX_DUP_NOTES]
    except Exception:
        logger.warning(
            "could not read duplicate_groups -- ignoring the field", exc_info=True
        )
        return [], [
            "`duplicate_groups` could not be read -- it was ignored (no case was "
            "removed)."
        ]


def _group_indices(cases: list, members: list) -> list:
    """Positions in ``cases`` of a group's members, first occurrence wins. Pure."""
    by_id: dict = {}
    for i, tc in enumerate(cases):
        by_id.setdefault(tc.tc_id, i)
    return [by_id[m] for m in members if m in by_id]


def _group_agreement(cases: list, members: list) -> float:
    """Lowest text agreement between a group's keeper and its other members."""
    idxs = _group_indices(cases, members)
    if len(idxs) < 2:
        return 0.0
    keep_idx = min(((i, cases[i]) for i in idxs), key=_dup_keeper_key)[0]
    ratios = [_dup_text_ratio(cases[i], cases[keep_idx]) for i in idxs if i != keep_idx]
    return min(ratios) if ratios else 0.0


def dup_agreements(cases: list, groups: list) -> list:
    """For each group, the LOWEST server-measured text agreement between its keeper
    and any other member -- an ADVISORY signal shown next to the group in the reply.

    WHY THIS IS NOT A GATE (measured, 2026-07-29, on this metric and on token
    Jaccard). The review asked for a lexical similarity FLOOR that a group must clear
    before a removal is honoured. It cannot be one: the metric does not separate the
    classes it would have to separate.

        pair                                            difflib  jaccard
        the MOTIVATING cross-category duplicate            0.29     0.25
          ("Cannot cancel another user's order by
           changing the order ID" vs "Attempt to cancel
           an order belonging to a different account")
        two UNRELATED same-domain cases                    0.28     0.05
        two UNRELATED cases                                0.34     0.14
        two near-identical boilerplate cases               0.97     0.82
        two boundary siblings (must NOT be merged)         0.95     0.83

    A genuine duplicate scores 0.29 while an unrelated pair scores 0.28-0.34, and a
    pair that must NOT be merged scores 0.95. Any floor high enough to reject the
    hostile pairs also rejects the exact duplicate that motivates the feature, and
    any floor low enough to admit it admits everything. Aggregating over a whole
    review does not rescue it either: on a templated 64-case suite the medians were
    0.97 genuine vs 0.57 hostile, but on hand-written text 0.29 vs 0.28 -- the scale
    is entirely corpus-dependent, which is exactly why this signal FLAGS instead
    of dropping.

    Shipping an uncalibrated number as a security bound on a DESTRUCTIVE path would
    be worse than shipping none: it would look like a guarantee. So the number is
    MEASURED and REPORTED (the tester gets the discriminating signal) while the
    actual bounds on removal are the two corpus-independent ones in
    ``screen_duplicate_groups``. Never raises.
    """
    out: list = []
    try:
        for members in groups or []:
            out.append(_group_agreement(cases or [], members or []))
    except Exception:
        logger.warning("dup_agreements failed -- omitting the labels", exc_info=True)
        return [0.0 for _ in (groups or [])]
    return out


def _screen_group_sizes(cases: list, groups: list, refusals: list) -> list:
    """Groups as ``[keeper_id, *others]``; an oversized group is refused (noted in
    ``refusals``, never truncated)."""
    screened: list = []
    for members in groups:
        idxs = _group_indices(cases, members)
        if len(idxs) < 2:
            continue
        if len(idxs) > _DUP_MAX_APPLY_GROUP_SIZE:
            if len(refusals) < _MAX_DUP_NOTES:
                refusals.append(
                    f"a group naming {len(idxs)} cases was NOT removed: more than "
                    f"{_DUP_MAX_APPLY_GROUP_SIZE} cases in one duplicate cluster "
                    "is not a duplicate, so it is reported for review instead."
                )
            continue
        keep_idx = min(((i, cases[i]) for i in idxs), key=_dup_keeper_key)[0]
        screened.append(
            [cases[keep_idx].tc_id] + [cases[i].tc_id for i in idxs if i != keep_idx]
        )
    return screened


def _removal_refusal(removable: int, total: int, limit: int) -> str:
    """Log and word the whole-review refusal for the proportional cap."""
    logger.warning(
        "host duplicate review refused: %d of %d cases proposed for removal (bound %d)",
        removable,
        total,
        limit,
    )
    return (
        f"REFUSED: the submitted duplicate review would remove {removable} "
        f"of {total} submitted case(s), above the "
        f"{_removal_ratio():.0%} safety bound ({limit} case(s)). NOTHING was "
        "removed and NO group is treated as a duplicate. A review that large "
        "is handled as untrusted input, not as a judgement -- the groups are "
        "still listed below for you to act on yourself."
    )


def screen_duplicate_groups(cases: list, groups: list) -> tuple[list, list]:
    """The DETERMINISTIC SAFETY SCREEN gating every REMOVAL. Returns
    ``(screened_groups, refusals)``. Called ONLY on the apply path.

    Both bounds are CORPUS-INDEPENDENT and need no calibration -- that is the whole
    point, because the field is attacker-influenced (the threat is not "an
    untrustworthy host model" but injected content inside the ``_GUARD``-wrapped
    Jira/comment text that host mode deliberately places in the host's context) and a
    tuned lexical threshold would be a guarantee in name only (see
    ``dup_agreements``).

    1. **APPLY-PATH GROUP-SIZE BOUND** (``_DUP_MAX_APPLY_GROUP_SIZE``). A group
       naming more than 4 cases is refused OUTRIGHT (never truncated -- partially
       honouring a group nobody vouched for is worse). Rationale from the design, not
       from a corpus: the fan-out has 8 categories and the project rule is one
       BEHAVIOUR per test, so a genuine cross-category duplicate cluster is 2 cases,
       occasionally 3. A 12-member group is not a duplicate cluster, it is a
       partition primitive. This alone cuts the theoretical removal set from
       50 x 11 = 550 ids to 50 x 3 = 150.
    2. **PROPORTIONAL CAP** (``_removal_ratio()``, default 35%, ceiling 40%). If the
       surviving groups would still remove more than that share of the SUBMITTED
       cases, the WHOLE review is refused -- nothing removed, no group honoured --
       and the refusal is reported verbatim. This is the bound that actually closes
       the disjoint-partition attack: whatever the text says, a 64-case suite cannot
       drop below 42 cases. ``max(1, ...)`` keeps a 2-case suite able to drop one
       real duplicate.

    Why the SHAPE caps in ``_extract_duplicate_groups`` are not enough: they permit
    50 x 12 = 550 removable ids, and their overlap rule only stops CHAINING, so 5
    DISJOINT groups of 12 would reduce a 64-case suite to 9. Never raises; on any
    failure NO group is honoured (the safe direction).
    """
    refusals: list = []
    if not cases or not groups:
        return [], refusals
    try:
        screened = _screen_group_sizes(cases, groups, refusals)
        removable = sum(len(g) - 1 for g in screened)
        limit = max(1, int(len(cases) * _removal_ratio()))
        if removable > limit:
            return [], [_removal_refusal(removable, len(cases), limit)]
        return screened, refusals
    except Exception:
        logger.warning(
            "screen_duplicate_groups failed -- honouring no group", exc_info=True
        )
        return [], [
            "`duplicate_groups` could not be screened -- no group was honoured and "
            "nothing was removed."
        ]


def _plan_drops(cases: list, groups: list) -> dict:
    """``{case index: keeper tc_id}`` for every non-keeper member of each group."""
    drop: dict = {}
    keepers: set = set()
    for members in groups or []:
        idxs = _group_indices(cases, members)
        if len(idxs) < 2:
            continue
        keep_idx = min(((i, cases[i]) for i in idxs), key=_dup_keeper_key)[0]
        keepers.add(keep_idx)
        for i in idxs:
            if i != keep_idx and i not in drop and i not in keepers:
                drop[i] = cases[keep_idx].tc_id
    return drop


def _rescue_sole_requirements(cases: list, drop: dict, notes: list) -> None:
    """Un-drop (in place) any case that is the only one tracing its requirement."""

    def _req(idx: int) -> str:
        return normalize_ac_id(getattr(cases[idx], "requirement_id", "") or "")

    covered = {_req(i) for i in range(len(cases)) if i not in drop and _req(i)}
    for i in sorted(drop):
        req = _req(i)
        if req and req not in covered:
            covered.add(req)
            drop.pop(i)
            if len(notes) < _MAX_DUP_NOTES:
                notes.append(
                    f"`{cases[i].tc_id}` was KEPT despite being grouped as a "
                    f"duplicate: it is the only case tracing {req}."
                )


def _log_removed(removed: int, total: int) -> None:
    if removed:
        logger.info(
            "host-reviewed dedup removed %d near-duplicate case(s) of %d",
            removed,
            total,
        )


def apply_duplicate_groups(cases: list, groups: list) -> tuple[list, list, list]:
    """REMOVE the non-keeper members of each ALREADY-SCREENED duplicate group.

    ``groups`` MUST be the output of ``screen_duplicate_groups`` -- this function
    applies no safety bound of its own. Only ever called when BOTH
    QA_HOST_DEDUP_REVIEW_ENABLED and QA_HOST_DEDUP_APPLY are on; the default
    behaviour is flag-only. MUST run BEFORE ``_finalize_generation``, which renumbers
    tc_ids. Returns ``(kept_cases, removed, notes)`` where removed is a list of
    ``(removed_tc_id, keeper_tc_id)`` pairs and notes discloses each rescue.

    NB-016 mirror (see ``agents.test_scenario_agent._dedupe_cases``): a
    member is NEVER removed while it is the only case
    tracing its ``requirement_id`` -- dropping it would flip that AC to a false
    ORPHAN in the RTM / AC-anchoring reports, which are built afterwards. Ids are
    compared through ``rtm.normalize_ac_id`` (``_dedupe_cases`` compares raw
    strings; the normalised form is the stricter of
    the two).

    This rescue is NOT a security bound and is not claimed as one: ``requirement_id``
    is a field of the HOST-SUBMITTED case, so a host emitting ``null`` (the common
    case) or the same id on every case rescues nothing. It defends against bad
    JUDGEMENT, not against a hostile submission -- ``screen_duplicate_groups`` does
    that. It also protects ``requirement_id`` ONLY, not atomic-checklist items; see
    the plan's checklist-interaction note.

    Never raises; on any failure every case is kept.
    """
    notes: list = []
    if not cases or not groups:
        return list(cases), [], notes
    try:
        drop = _plan_drops(cases, groups)
        _rescue_sole_requirements(cases, drop, notes)
        kept = [tc for i, tc in enumerate(cases) if i not in drop]
        if not kept:  # unreachable (a keeper is always kept); belt-and-braces
            return list(cases), [], notes
        removed = [(cases[i].tc_id, drop[i]) for i in sorted(drop)]
        _log_removed(len(removed), len(cases))
        return kept, removed, notes
    except Exception:
        logger.warning(
            "apply_duplicate_groups failed -- keeping every case", exc_info=True
        )
        return list(cases), [], notes


def _render_dup_member(m, sub_by_id: dict, final_by_stable: dict, removed_set: set):
    """One group member as the tester will see it. Pure."""
    tc = sub_by_id.get(m)
    title = ((getattr(tc, "title", "") or "") if tc else "")[:80]
    sid = (getattr(tc, "stable_id", "") or "") if tc else ""
    final_id = final_by_stable.get(sid) if sid else ""
    if m in removed_set:
        return f'`{m}` "{title}" -- REMOVED as a duplicate'
    if final_id:
        return f'`{final_id}` "{title}" (submitted as `{m}`)'
    return (
        f'`{m}` "{title}" -- not in the final suite (already '
        "collapsed as an exact duplicate)"
    )


def _render_dup_group_lines(
    groups: list, render_member, agreements: list, low: float
) -> list:
    """The bullet line per group, cut at ``_MAX_DUP_SECTION_CHARS`` characters
    (always at least one line). ``render_member`` maps a tc_id to its text. Pure."""
    budget = _MAX_DUP_SECTION_CHARS
    out: list = []
    for gi, members in enumerate(groups):
        rendered = [render_member(m) for m in members]
        label = ""
        if gi < len(agreements):
            score = agreements[gi]
            flag = " — LOW, review before trusting" if score < low else ""
            label = f" _(agreement {score:.2f}{flag})_"
        line = "- " + "; ".join(rendered) + label
        if out and len(line) > budget:
            break
        budget -= len(line)
        out.append(line)
    return out


def _member_renderer(submitted_cases: list, final_cases: list, removed: list):
    """A tc_id -> display text function bound to the submitted/final suites."""
    sub_by_id = {tc.tc_id: tc for tc in (submitted_cases or [])}
    final_by_stable: dict = {}
    for tc in final_cases or []:
        sid = getattr(tc, "stable_id", "") or ""
        if sid:
            final_by_stable.setdefault(sid, tc.tc_id)
    removed_set = {r[0] for r in removed if r}
    return lambda m: _render_dup_member(m, sub_by_id, final_by_stable, removed_set)


def _dup_review_intro_lines(n_groups: int, removed: list, applied: bool) -> list:
    """Heading plus the explanatory paragraph(s) above the group list."""
    if applied and removed:
        state = "cases REMOVED"
    elif applied:
        state = "APPLY ON -- nothing met the safety bounds, nothing removed"
    else:
        state = "REPORTED ONLY -- nothing removed"
    lines = [f"## ♻️ Duplicate review ({state})", ""]
    lines.append(
        f"Your chat model grouped **{n_groups}** set(s) of submitted cases "
        "as verifying the same behaviour."
    )
    if applied and removed:
        lines.append(
            f"**{len(removed)}** case(s) were removed -- one representative "
            "kept per group (highest priority, earliest submitted). Every "
            "removal passed two deterministic server-side bounds: no cluster "
            f"larger than {_DUP_MAX_APPLY_GROUP_SIZE} cases, and no more than "
            f"{_removal_ratio():.0%} of the submitted suite removed in total."
        )
    elif not applied:
        lines.append(
            "Nothing was deleted. A near-duplicate judgement has no "
            "calibrated precision, and removing a case that is not really a "
            "duplicate destroys coverage a tester cannot recover -- so this "
            "is advisory. Review the groups below, or set "
            "QA_HOST_DEDUP_APPLY=true to let the server drop them (still "
            "subject to the same two bounds)."
        )
    lines.append(
        "*Agreement* is a server-measured textual similarity, shown so you "
        "can spot a grouping the wording does not support. It is a reading "
        "aid, NOT a correctness check: a genuine duplicate phrased "
        "differently can score low, so it never decides anything."
    )
    lines.append("")
    return lines


def _dup_truncation_lines(n_hidden: int, removed_ids: list) -> list:
    """The "…and N more" line, plus every removed id (a cut list never hides a deletion)."""
    lines = [
        f"- …and {n_hidden} more group(s); the list is "
        f"truncated at ~{_MAX_DUP_SECTION_CHARS} characters."
    ]
    if removed_ids:
        head = ", ".join(f"`{i}`" for i in removed_ids[:_MAX_DUP_REMOVED_IDS])
        extra = (
            f" (+{len(removed_ids) - _MAX_DUP_REMOVED_IDS} more)"
            if len(removed_ids) > _MAX_DUP_REMOVED_IDS
            else ""
        )
        lines += [
            "",
            "**Every removed case id** (listed in full because the group "
            f"list above was truncated): {head}{extra}",
        ]
    return lines


def _dup_review_lines(
    groups: list, removed: list, applied: bool, agreements: list, render_member
) -> list:
    """The whole review block: intro, bounded group list, truncation disclosure."""
    lines = _dup_review_intro_lines(len(groups), removed, applied)
    group_lines = _render_dup_group_lines(
        groups, render_member, agreements, _low_text_ratio()
    )
    lines += group_lines
    if len(group_lines) < len(groups):
        removed_ids = [r[0] for r in removed if r]
        lines += _dup_truncation_lines(len(groups) - len(group_lines), removed_ids)
    lines.append("")
    return lines


def _dup_notes_lines(notes: list) -> list:
    """The quoted notes block (UNTRUSTED field, capped at ``_MAX_DUP_NOTES``)."""
    lines = [
        "> ℹ️  Duplicate-review notes (the field is UNTRUSTED and is "
        "screened server-side):"
    ]
    lines += [f">   - {n}" for n in notes[:_MAX_DUP_NOTES]]
    lines.append("")
    return lines


def build_duplicate_section(
    groups: list,
    submitted_cases: list,
    final_cases: list,
    *,
    removed: list | None = None,
    applied: bool = False,
    notes: list | None = None,
    agreements: list | None = None,
) -> str:
    """The bounded, deterministic "duplicate review" block prepended to the submit
    reply, AHEAD of the variable-length generated summary (the same ordering rule
    that moved ``quality_section`` in front of ``checklist_section``).

    Each submitted tc_id is resolved to the tc_id the tester will actually SEE:
    ``_finalize_generation`` renumbers ids, so the mapping goes through the case's
    content ``stable_id``, which survives both dedup and the renumber (the renumber
    uses ``model_copy``, which does not re-derive it). A member that is not in the
    final suite is NAMED as such instead of being silently dropped.

    ``agreements[i]`` is the ADVISORY server-measured text agreement for
    ``groups[i]``; a group below ``_low_text_ratio()`` is LABELLED so the tester can
    see which grouping the text does not support. It is a label, never a veto -- see
    ``dup_agreements`` for why.

    Bounded by CHARACTERS (``_MAX_DUP_SECTION_CHARS``), not by group count, so the
    section cannot grow to tens of KB. Truncation never hides a deletion: whenever
    the group list is cut AND cases were removed, every removed tc_id is listed
    (itself bounded, because the proportional cap bounds how many there can be).
    Pure and synchronous. Never raises.
    """
    try:
        groups = list(groups or [])
        notes = list(notes or [])
        removed = list(removed or [])
        agreements = list(agreements or [])
        if not groups and not notes and not removed:
            return ""
        lines: list = []
        if groups or removed:
            render_member = _member_renderer(submitted_cases, final_cases, removed)
            lines += _dup_review_lines(
                groups, removed, applied, agreements, render_member
            )
        if notes:
            lines += _dup_notes_lines(notes)
        return "\n".join(lines) + "\n"
    except Exception:
        logger.warning(
            "build_duplicate_section failed -- omitting the section", exc_info=True
        )
        return ""
