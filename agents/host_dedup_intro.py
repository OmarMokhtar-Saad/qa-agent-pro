"""The heading and explanatory paragraphs above the duplicate group list."""

from __future__ import annotations

from agents.host_dedup_consts import (
    _DUP_MAX_APPLY_GROUP_SIZE,
    _MAX_DUP_REMOVED_IDS,
    _MAX_DUP_SECTION_CHARS,
)
from agents.host_dedup_limits import _removal_ratio

_DUP_ADVISORY_PARAGRAPH = (
    "Nothing was deleted. A near-duplicate judgement has no "
    "calibrated precision, and removing a case that is not really a "
    "duplicate destroys coverage a tester cannot recover -- so this "
    "is advisory. Review the groups below, or set "
    "QA_HOST_DEDUP_APPLY=true to let the server drop them (still "
    "subject to the same two bounds)."
)
_DUP_AGREEMENT_PARAGRAPH = (
    "*Agreement* is a server-measured textual similarity, shown so you "
    "can spot a grouping the wording does not support. It is a reading "
    "aid, NOT a correctness check: a genuine duplicate phrased "
    "differently can score low, so it never decides anything."
)


def _dup_state(removed: list, applied: bool) -> str:
    """The parenthesised state in the review heading."""
    if applied and removed:
        return "cases REMOVED"
    if applied:
        return "APPLY ON -- nothing met the safety bounds, nothing removed"
    return "REPORTED ONLY -- nothing removed"


def _dup_applied_paragraph(removed: list, applied: bool) -> str | None:
    """The paragraph that says what was (not) deleted, or None when neither case applies."""
    if applied and removed:
        return (
            f"**{len(removed)}** case(s) were removed -- one representative "
            "kept per group (highest priority, earliest submitted). Every "
            "removal passed two deterministic server-side bounds: no cluster "
            f"larger than {_DUP_MAX_APPLY_GROUP_SIZE} cases, and no more than "
            f"{_removal_ratio():.0%} of the submitted suite removed in total."
        )
    if not applied:
        return _DUP_ADVISORY_PARAGRAPH
    return None


def _dup_review_intro_lines(n_groups: int, removed: list, applied: bool) -> list:
    """Heading plus the explanatory paragraph(s) above the group list."""
    lines = [f"## ♻️ Duplicate review ({_dup_state(removed, applied)})", ""]
    lines.append(
        f"Your chat model grouped **{n_groups}** set(s) of submitted cases "
        "as verifying the same behaviour."
    )
    paragraph = _dup_applied_paragraph(removed, applied)
    if paragraph is not None:
        lines.append(paragraph)
    lines.append(_DUP_AGREEMENT_PARAGRAPH)
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
