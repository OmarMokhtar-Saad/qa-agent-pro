"""Rendering of the duplicate review block and the ``DupReviewOutcome`` it reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from agents.host_dedup_consts import _MAX_DUP_NOTES, _MAX_DUP_SECTION_CHARS
from agents.host_dedup_intro import _dup_review_intro_lines, _dup_truncation_lines
from agents.host_dedup_limits import _low_text_ratio


@dataclass(frozen=True)
class DupReviewOutcome:
    """What the dedup pass did, as ``build_duplicate_section`` reports it."""

    removed: list | None = None
    applied: bool = False
    notes: list | None = None
    agreements: list | None = None


class DupSuites(NamedTuple):
    """The submitted and the final case lists ``build_duplicate_section`` maps ids across."""

    submitted: list
    final: list


def _render_dup_member(m, ctx: tuple):
    """One group member as the tester will see it. Pure.
    ``ctx`` is ``(sub_by_id, final_by_stable, removed_set)``."""
    sub_by_id, final_by_stable, removed_set = ctx
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


def _render_dup_group_lines(groups: list, render_member, scoring: tuple) -> list:
    """The bullet line per group, cut at ``_MAX_DUP_SECTION_CHARS`` characters
    (always at least one line). ``render_member`` maps a tc_id to its text.
    ``scoring`` is ``(agreements, low)``. Pure."""
    agreements, low = scoring
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
    ctx = (sub_by_id, final_by_stable, removed_set)
    return lambda m: _render_dup_member(m, ctx)


def _dup_review_lines(groups: list, view: tuple, render_member) -> list:
    """The whole review block: intro, bounded group list, truncation disclosure.
    ``view`` is ``(removed, applied, agreements)``."""
    removed, applied, agreements = view
    lines = _dup_review_intro_lines(len(groups), removed, applied)
    group_lines = _render_dup_group_lines(
        groups, render_member, (agreements, _low_text_ratio())
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
