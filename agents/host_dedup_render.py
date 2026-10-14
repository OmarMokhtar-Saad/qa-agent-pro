"""The reply sections that print the candidate duplicate pairs."""

from __future__ import annotations

from agents.host_dedup_consts import _UNTRUSTED_ERRORS, logger
from agents.host_dedup_pairs import _DUP_SHORTLIST_MAX_PAIRS, _DUP_SHORTLIST_TITLE_CHARS
from config.settings_guard import never_raise


def shortlist_safe(text: object, cap: int) -> str:
    """Sanitise UNTRUSTED host text for interpolation into the reply: strip
    backticks and newlines (backtick-span breakout) and cap the length."""
    try:
        return str(text or "").replace("`", "").replace("\n", " ").strip()[:cap]
    except _UNTRUSTED_ERRORS:  # pragma: no cover
        return ""


_SHORTLIST_INTRO = (
    "",
    "### \U0001f50d Candidate duplicate pairs (server lexical prescreen -- ADVISORY)",
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
)


def _shortlist_intro_lines() -> list:
    """The heading and call to action above the shortlist rows."""
    return list(_SHORTLIST_INTRO)


def _pairs_intro_lines() -> list:
    """The heading and caveat above the contradiction-evidence rows."""
    return [
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


def _row_fields(p: dict) -> tuple:
    """The four sanitised id/title strings of one pair row (UNTRUSTED host text)."""
    return (
        shortlist_safe(p.get("id_a"), 16),
        shortlist_safe(p.get("id_b"), 16),
        shortlist_safe(p.get("title_a"), _DUP_SHORTLIST_TITLE_CHARS),
        shortlist_safe(p.get("title_b"), _DUP_SHORTLIST_TITLE_CHARS),
    )


def _row_ratio(p: dict) -> float:
    """The row's agreement ratio, 0.0 when it is not a number."""
    try:
        return float(p.get("ratio") or 0.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _row_line(fields: tuple, ratio: float) -> str:
    """One bullet line for a pair row."""
    id_a, id_b, t_a, t_b = fields
    return f'- `{id_a}` "{t_a}" ~ `{id_b}` "{t_b}" (lexical agreement {ratio:.2f})'


def build_dup_shortlist_section(pairs: list) -> str:
    """Markdown appendix for the qa_submit_category reply that completed the
    expected set. "" when there are no pairs. Titles are sanitised (UNTRUSTED
    host text) and the ids shown are POST-MERGE GLOBAL tc_ids, which the
    finalize sidecar passes through unchanged. Never raises."""
    try:
        if not pairs:
            return ""
        lines = _shortlist_intro_lines()
        for p in pairs[:_DUP_SHORTLIST_MAX_PAIRS]:
            if not isinstance(p, dict):
                continue
            lines.append(_row_line(_row_fields(p), _row_ratio(p)))
        lines.append("")
        return "\n".join(lines) + "\n"
    except _UNTRUSTED_ERRORS:
        logger.debug("build_dup_shortlist_section failed", exc_info=True)
        return ""


# The TRIMMABLE finalize-reply EVIDENCE behind the headline above: the
# candidate pairs themselves. "" when there are none.
#
# Deliberately NOT ``build_dup_shortlist_section``. That renderer's body is a
# call to action for a host still mid-flight ("confirm a shortlist via the
# finalize sidecar", "finalize with duplicate_groups: []"), which on a
# finalize reply is unfollowable -- the suite is validated, exported and
# persisted by the time this is read. Same measure, same sanitiser, different
# audience, and no dead instruction.
#
# Ids and titles are UNTRUSTED host text: every interpolation goes through
# ``shortlist_safe`` (backtick-span breakout and newlines stripped, length
# capped). Pure, synchronous, stdlib only. Never raises.
def build_dup_contradiction_pairs(pairs: list) -> str:
    """The TRIMMABLE finalize-reply EVIDENCE: the candidate pairs themselves. "" when none."""
    # public boundary; tests pin that nothing escapes
    with never_raise(logger, "build_dup_contradiction_pairs failed", exc_info=True):
        rows = [p for p in (pairs or []) if isinstance(p, dict)]
        if not rows:
            return ""
        lines = _pairs_intro_lines()
        for p in rows[:_DUP_SHORTLIST_MAX_PAIRS]:
            ratio = _row_ratio(p)
            lines.append(_row_line(_row_fields(p), ratio))
        lines.append("")
        return "\n".join(lines) + "\n"
    return ""
