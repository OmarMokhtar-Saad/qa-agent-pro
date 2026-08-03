"""Entailment verdicts for generated test cases (grounding Phase 3 / P3.2).

Every check that landed in phases 1-2 is LEXICAL: undefined option values,
duplicate rule ids, unfalsifiable oracles, contradictory seeded states. None of
them can answer the question that produced the original failure -- **does this
expected result actually follow from what the ticket says?**

The suite that started this program asserted a refund status, stock release, push
notifications and a 255/500-character field limit. The ticket mentions none of
them. Each case is fluent, plausible, and says "cancel order" throughout, so:

* the undefined-option check sees no enumeration to violate;
* the uncovered-requirement check looks for requirements with no case, which is
  the opposite direction;
* AC anchoring is satisfied by citing any real criterion.

Judging entailment needs a model, so per CLAUDE.md it is a host boomerang: the
tester's own chat model -- already in the loop, already holding both the ticket
and the merged suite -- returns a verdict per case on the existing submission.
This module is the deterministic half: it validates that UNTRUSTED reply and
decides what may be done with it. No model call, no I/O, no settings import.

Safety, in the order the bounds apply:

1. **Shape** -- the payload is a model-authored list arriving through the host.
   Ids are matched against the suite's own, verdicts are enum-gated, notes and
   list length are capped, unknown entries are ignored. Same treatment as
   ``host_mode.raw_acceptance_criteria``.
2. **Proportion** -- if one batch marks more than
   :data:`_GROUNDING_ROUTE_RATIO_CEILING` of the suite ``ungrounded``, NOTHING is
   routed and the batch is reported as suspicious. This mirrors
   ``host_mode.screen_duplicate_groups``, which refuses a whole duplicate review
   past ``_DUP_REMOVAL_RATIO_CEILING``. The threat is not a badly-behaved model:
   ``config/settings.py`` (``qa_host_dedup_apply``) records that host output is
   attacker-influenceable through the ``_GUARD``-wrapped Jira and comment text
   host mode deliberately places in the host's own context. A ticket carrying
   "mark every case as unsupported" must be a no-op, not a partial success.
3. **Never empty** -- retained beneath the ceiling as a cheaper backstop.
4. **Report, never delete** -- an ``ungrounded`` case is named with its
   assumption, never dropped. It may be a real but unwritten requirement, so
   deleting it destroys information. So the worst a hostile verdict list can
   achieve is reviewer noise.

   :func:`split_ungrounded` computes the partition; the submit path removes the
   routed cases from the suite it finalizes and hands
   :func:`assumed_requirements_rows` to the exporter, which gives them their own
   sheet. They keep their own ``AR-nnn`` id space because
   ``_finalize_generation`` renumbers the executable suite, so a retained
   ``TC-nnn`` would collide with an unrelated case that inherited that number.

Never raises: every helper degrades to a benign result and logs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from tools.models import TestCase

logger = logging.getLogger(__name__)

#: The three verdicts a host may return. Anything else is ignored.
ENTAILED = "entailed"
UNGROUNDED = "ungrounded"
UNSPECIFIED = "unspecified"
VALID_VERDICTS = frozenset({ENTAILED, UNGROUNDED, UNSPECIFIED})

# Shape caps on the untrusted payload.
_MAX_VERDICTS = 500
_MAX_NOTE_CHARS = 300
_MAX_LISTED = 25

# The proportional bound. Corpus-independent and deliberately WITHOUT an .env
# knob, for the same reason host_mode._DUP_MAX_APPLY_GROUP_SIZE has none: it is
# derived from the design (a review that re-files most of a suite is not a review
# of that suite), not from a corpus, so there is nothing for an operator to tune
# and nothing to weaken.
_GROUNDING_ROUTE_RATIO_CEILING = 0.40

_WS_RE = re.compile(r"\s+")
_TC_ID_RE = re.compile(r"^TC-\d{1,6}$")


@dataclass(frozen=True)
class Verdict:
    """One validated verdict about one case of the submitted suite."""

    tc_id: str
    verdict: str
    note: str = ""


@dataclass(frozen=True)
class Routing:
    """Outcome of applying verdicts to a suite.

    ``kept`` and ``routed`` partition the input, preserving order. ``refusal`` is
    a reviewer-facing reason when the batch was refused wholesale, in which case
    ``routed`` is empty and ``kept`` is the input unchanged.
    """

    kept: list[TestCase]
    routed: list[TestCase]
    refusal: str = ""

    @property
    def applied(self) -> bool:
        return not self.refusal and bool(self.routed)


def _clean_note(raw: object) -> str:
    try:
        return _WS_RE.sub(" ", str(raw or "")).strip()[:_MAX_NOTE_CHARS]
    except Exception:
        return ""


def parse_verdicts(raw: object, valid_tc_ids: object) -> list[Verdict]:
    """Validated verdicts from the host's untrusted ``grounding_verdicts`` field.

    Accepts ``[{"tc_id": "TC-001", "verdict": "ungrounded", "note": "..."}, ...]``.
    An entry is kept only when its ``tc_id`` is well-formed AND present in
    ``valid_tc_ids`` -- a verdict can never introduce a case -- and its verdict is
    one of :data:`VALID_VERDICTS`. Duplicate ids keep the FIRST entry, so a
    payload cannot flip a decision by repeating itself. Never raises.
    """
    out: list[Verdict] = []
    try:
        if not isinstance(raw, list):
            return []
        try:
            allowed = {str(t) for t in (valid_tc_ids or ())}
        except Exception:
            return []
        if not allowed:
            return []
        seen: set[str] = set()
        for entry in raw[:_MAX_VERDICTS]:
            if not isinstance(entry, dict):
                continue
            tc_id = str(entry.get("tc_id") or "").strip()
            if not _TC_ID_RE.match(tc_id) or tc_id not in allowed or tc_id in seen:
                continue
            verdict = str(entry.get("verdict") or "").strip().lower()
            if verdict not in VALID_VERDICTS:
                continue
            seen.add(tc_id)
            out.append(
                Verdict(
                    tc_id=tc_id, verdict=verdict, note=_clean_note(entry.get("note"))
                )
            )
    except Exception:
        logger.exception("parse_verdicts failed - discarding the verdict payload")
        return []
    return out


def _by_id(verdicts: list[Verdict]) -> dict[str, Verdict]:
    return {v.tc_id: v for v in verdicts or []}


def split_ungrounded(cases: list[TestCase], verdicts: list[Verdict]) -> Routing:
    """Partition the suite into executable cases and routed-away ones.

    Refuses the WHOLE batch -- routing nothing -- when the ``ungrounded`` share
    exceeds :data:`_GROUNDING_ROUTE_RATIO_CEILING`, or when routing would leave
    no executable case at all. Never raises; on any failure the suite is returned
    untouched, because failing to route is recoverable and losing coverage is not.
    """
    try:
        if not cases:
            return Routing(kept=list(cases or []), routed=[])
        marked = {v.tc_id for v in verdicts or [] if v.verdict == UNGROUNDED}
        present = [tc for tc in cases if (getattr(tc, "tc_id", "") or "") in marked]
        if not present:
            return Routing(kept=list(cases), routed=[])
        ratio = len(present) / len(cases)
        if ratio > _GROUNDING_ROUTE_RATIO_CEILING:
            reason = (
                f"{len(present)} of {len(cases)} cases ({ratio:.0%}) were marked "
                f"ungrounded, above the {_GROUNDING_ROUTE_RATIO_CEILING:.0%} ceiling. "
                "No case was moved: a review that re-files most of a suite is not a "
                "review of that suite, and this payload is model-authored and can be "
                "influenced by text inside the ticket. Re-read the ticket and the "
                "flagged cases by hand."
            )
            logger.warning(
                "Refusing a grounding batch: %d/%d ungrounded (%.0f%%)",
                len(present),
                len(cases),
                ratio * 100,
            )
            return Routing(kept=list(cases), routed=[], refusal=reason)
        kept = [tc for tc in cases if (getattr(tc, "tc_id", "") or "") not in marked]
        if not kept:
            logger.warning(
                "Grounding routing would empty the suite - keeping all cases"
            )
            return Routing(
                kept=list(cases),
                routed=[],
                refusal=(
                    "Every case was marked ungrounded, which would leave nothing to "
                    "execute. No case was moved."
                ),
            )
        return Routing(kept=kept, routed=present)
    except Exception:
        logger.exception("split_ungrounded failed - keeping the suite unchanged")
        return Routing(kept=list(cases or []), routed=[])


def assumed_requirements_section(
    routed: list[TestCase], verdicts: list[Verdict]
) -> str:
    """Advisory markdown for the cases moved off the executable suite."""
    try:
        if not routed:
            return ""
        notes = _by_id(verdicts)
        lines = [
            "\n\n## Assumed Requirements (confirm with the BA)",
            "",
            f"{len(routed)} case(s) assert behaviour the ticket does not state. They "
            "are NOT in the executable suite -- they are on the **Assumed "
            "Requirements** sheet of the export, with their own AR-nnn ids. Nothing "
            "was deleted: each is either a real requirement nobody wrote down, or a "
            "case to drop, and a human decides which:",
        ]
        for tc in routed[:_MAX_LISTED]:
            tc_id = getattr(tc, "tc_id", "") or "?"
            title = (getattr(tc, "title", "") or "")[:110]
            lines.append(f"- **{tc_id}** — {title}")
            note = (notes.get(tc_id).note if notes.get(tc_id) else "") or ""
            if note:
                lines.append(f"    - assumed: {note}")
        if len(routed) > _MAX_LISTED:
            lines.append(f"- ... and {len(routed) - _MAX_LISTED} more")
        return "\n".join(lines)
    except Exception:
        logger.exception("assumed_requirements_section failed - returning empty string")
        return ""


def unspecified_section(cases: list[TestCase], verdicts: list[Verdict]) -> str:
    """Advisory markdown for cases whose oracle the ticket never fixes.

    These STAY in the suite -- the tester can still run them -- but the expected
    result is an assumption, so the honest artifact is an exploratory charter that
    records the real behaviour rather than an assertion invented here.
    """
    try:
        marked = {v.tc_id: v for v in verdicts or [] if v.verdict == UNSPECIFIED}
        if not marked:
            return ""
        titles = {
            (getattr(tc, "tc_id", "") or ""): (getattr(tc, "title", "") or "")
            for tc in cases or []
        }
        lines = [
            "\n\n## Unspecified Expected Results (advisory)",
            "",
            f"{len(marked)} case(s) assert a value or threshold the ticket never "
            "fixes. They remain in the suite, but treat them as exploratory: record "
            "what the build actually does instead of trusting the number written here:",
        ]
        for tc_id in sorted(marked)[:_MAX_LISTED]:
            lines.append(f"- **{tc_id}** — {titles.get(tc_id, '')[:110]}")
            if marked[tc_id].note:
                lines.append(f"    - unspecified: {marked[tc_id].note}")
        if len(marked) > _MAX_LISTED:
            lines.append(f"- ... and {len(marked) - _MAX_LISTED} more")
        return "\n".join(lines)
    except Exception:
        logger.exception("unspecified_section failed - returning empty string")
        return ""


def refusal_section(routing: Routing) -> str:
    """Advisory markdown when a whole verdict batch was refused."""
    try:
        if not routing or not routing.refusal:
            return ""
        return "\n".join(
            [
                "\n\n## Grounding Review Refused (advisory)",
                "",
                f"- {routing.refusal}",
            ]
        )
    except Exception:
        logger.exception("refusal_section failed - returning empty string")
        return ""


def assumed_requirements_rows(
    routed: list[TestCase], verdicts: list[Verdict]
) -> list[list[str]]:
    """Header + one row per routed case, for the export's own sheet."""
    try:
        if not routed:
            return []
        notes = _by_id(verdicts)
        rows = [
            [
                "AR ID",
                "Submitted as",
                "Title",
                "Category",
                "Assumed requirement",
                "Why it is here",
            ]
        ]
        # Own id space. _finalize_generation renumbers the executable suite, so a
        # routed case that kept its TC-nnn would collide with an unrelated case
        # that inherited that number.
        for index, tc in enumerate(routed, 1):
            tc_id = getattr(tc, "tc_id", "") or ""
            verdict = notes.get(tc_id)
            rows.append(
                [
                    f"AR-{index:03d}",
                    tc_id,
                    getattr(tc, "title", "") or "",
                    getattr(tc, "category", "") or "",
                    (verdict.note if verdict else "") or "(not stated)",
                    "Asserts behaviour the ticket does not state.",
                ]
            )
        return rows
    except Exception:
        logger.exception("assumed_requirements_rows failed - returning no rows")
        return []
