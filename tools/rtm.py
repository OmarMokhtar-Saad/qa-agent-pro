"""Requirements Traceability Matrix helpers.

Parses acceptance criteria text into numbered AcceptanceCriterion items
and builds a markdown RTM coverage summary.

Never raises — all functions return empty results on failure.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from dataclasses import field as dc_field

from config.settings import settings
from tools.atomic_checklist import (
    HONESTY_BOUNDARY,
    PROVENANCE_LIMITATION,
    lexical_cosine_matrix,
    provenance_caveats,
)
from tools.embeddings import backend_enabled, cosine_similarity, embed_texts
from tools.models import TestCase, normalize_ac_id

logger = logging.getLogger(__name__)

# `_NLI_LEDGER_ID = "rtm.nli_verdicts"` stood here until 2026-08-16 (dead-code
# deletion P2-G1), together with the two OPTIONAL LLM tiers it tagged. Its
# sibling `_AC_LEDGER_ID` ("rtm.acceptance_criteria") went in P2-F2. Both ids
# STAY in tools/host_llm.LEDGER_IDS -- that frozenset never shrinks, because an
# id is what keeps "this path migrated / was disabled" checkable after the code
# is gone. There is nothing left in this module to tag: it makes no LLM call.


@dataclass
class AcceptanceCriterion:
    ac_id: str
    description: str


# generate_acs lived here until 2026-08-16 (dead-code deletion P2-F2),
# together with its _GeneratedAC / _GeneratedACList response models, the
# _AC_GEN_SYSTEM prompt and the ledger id `rtm.acceptance_criteria`. It made
# ONE server-side llm.ask_json call synthesizing acceptance criteria for the
# 3-of-4 input types that carry none, so the RTM could light up.
#
# It was dead. Its only caller was agents/test_scenario_agent._run_gen_acs,
# which ran only under `synthesize_acs=True`; the one live caller of
# _prepare_generation (tools/mcp_handlers.handle_prepare_test_cases) passed
# synthesize_acs=not _host_ac, and `_host_ac` derives from
# llm.resolve_generation_mode() == "host" -- a constant since 2026-08-12. The
# legacy routes that still reached it, graph.py and evals/, were deleted in
# P2-A and P2-B.
#
# There is NO capability loss: the criteria are derived by the tester's OWN
# model as agents/host_mode.AC_JOB, and _prepare_generation still appends
# _HOST_AC_JOB_DIRECTIVE to rtm_hint to ask for them. parse_acceptance_criteria,
# rtm_trace, match_checklist and the rest of this module are LIVE and untouched.


# ``normalize_ac_id`` MOVED to tools/models.py on 2026-08-19 (F06) and is
# re-exported by the import above, so `from tools.rtm import normalize_ac_id`
# still resolves for tools/ac_anchor.py, agents/host_mode.py,
# agents/test_scenario_agent.py and tests/test_rtm.py -- none of them changed.
# It moved because its sibling ``display_requirement_id`` is needed by all five
# exporters on every row, and importing THIS module to reach it would have pulled
# tools.atomic_checklist and tools.embeddings into csv_exporter /
# gherkin_exporter / playwright_exporter, which need neither.


# Values that are NOT acceptance criteria even when a configured field returns
# them. `settings.jira_ac_field` defaults to customfield_10016, which is a DATE
# field on some Jira instances: SHYJ-5645 returned
# "2025-09-11T09:07:21.362+0300", that truthy value suppressed description
# scanning, and the generator was handed a timestamp as its only requirement to
# trace against -- so nothing downstream could tell a grounded case from an
# invented one.
_ISO_DATEISH_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
_SLASH_DATEISH_RE = re.compile(r"^\d{1,4}[/.]\d{1,2}[/.]\d{1,4}$")
_NUMERIC_RE = re.compile(r"^[\d.,%+-]+$")


def looks_like_requirement_text(text: object) -> bool:
    """True when a string could be a testable acceptance criterion.

    Deliberately NARROW -- it rejects only what cannot possibly be a
    requirement, because a false rejection silently discards a real criterion:

    * a date or ISO-8601 timestamp (the observed failure);
    * a value with no whitespace at all -- a requirement is a sentence, and this
      is what separates a timestamp or a field id from prose;
    * a purely numeric/punctuation value;
    * a value with no letters in any script.

    Multi-word criteria that merely START with a number are UNAFFECTED, so
    NB-005 behaviour ("200ms response time", "3 failed logins locks the
    account") is preserved. Never raises.
    """
    try:
        if not isinstance(text, str):
            return False
        value = text.strip()
        if not value:
            return False
        if _ISO_DATEISH_RE.match(value) or _SLASH_DATEISH_RE.match(value):
            return False
        if _NUMERIC_RE.match(value):
            return False
        if not re.search(r"[^\W\d_]", value, re.UNICODE):
            return False
        # A requirement is a sentence; a single unbroken token is an id, a date,
        # or a label -- never a testable condition.
        return bool(re.search(r"\s", value))
    except Exception:
        logger.exception(
            "looks_like_requirement_text failed - treating as non-requirement"
        )
        return False


# `AC1:` / `AC-2.` / `AC 03)` -- the label form acceptance criteria are most
# often written in, and the one the split below used to miss ENTIRELY. A ticket
# whose criteria were written `AC1:`..`AC10:` followed by a trailing `Notes:`
# paragraph parsed as ONE criterion plus the Notes line, and the RTM then
# reported "2/2 acceptance criteria traced, all covered" over a ten-criterion
# ticket: a silent under-count that fails GREEN (F2, live run 2026-08-30).
#
# The trailing paragraph is what disabled the recovery path: the single-newline
# fallback fired only when the paragraph split produced <= 1 chunk, so any
# trailing prose -- Notes, links, a sign-off -- silently switched it off.
# 2026-08-31 (F3): the label was AC-ONLY. A ticket whose criteria are written
# `BR01:`..`BR14:` -- inline, on ONE line, which is exactly what a Business
# Rules table flattens to -- matched nothing, split nothing, and produced ONE
# criterion. The traceability sheet then read "0/1 traced, 66 orphans" over a
# fourteen-rule ticket (measured, SHYJ-10051). The 2026-08-30 fix for this
# failure was written for the `ACn` INSTANCE; this is the class.
#
# Two guards keep the wider label safe: an inline split is only taken when at
# least two labels are present (so a lone "BR01" quoted in prose cannot shatter
# a paragraph), and only an `ACn` marker is STRIPPED from the resulting text --
# a `BR07:` prefix is deliberately KEPT so _trace_map can match a case tagged
# "BR07" back to the criterion it names.
_LABEL_BODY = r"[A-Z]{2,5}\s*-?\s*\d{1,3}\s*[:.)\]-]\s+"
_AC_LABEL_RE = re.compile(rf"(?m)^\s*{_LABEL_BODY}")
_ANY_LABEL_RE = re.compile(_LABEL_BODY)
_INLINE_LABEL_SPLIT_RE = re.compile(rf"(?=\b{_LABEL_BODY})")
_MIN_INLINE_LABELS = 2
_AC_SPLIT_RE = (
    r"(?m)(?:^\s*[-*•]\s+|^\s*\d+[.)\]]\s+"
    rf"|^\s*{_LABEL_BODY})|\n{{2,}}"
)

# Section labels that introduce prose ABOUT the ticket rather than a criterion.
# Admitting one creates a bogus AC that every downstream anchoring check then
# treats as ground truth -- the same class the numeric/date guard above exists
# for, reached through a different door.
_NON_AC_PREFIX_RE = re.compile(
    r"^\s*(notes?|links?|references?|out of scope|scope|assumptions?|context)\s*:",
    re.IGNORECASE,
)


# 2026-08-31 (F3): splitting a Business-Rules table inline leaves the table's
# own heading as the first chunk ("Business Rules:" with nothing after it). It
# is a section label, not a criterion. Matched ONLY when the line is the bare
# heading: a UC-table row reading "Business Rules: BR02: not all products have
# a cancelation service" carries a real requirement after the colon and must
# survive -- an earlier revision of this fix dropped it and cost a criterion.
_BARE_SECTION_RE = re.compile(
    r"^\s*(business rules?|acceptance criteria|rules?)\s*:?\s*$", re.IGNORECASE
)


def parse_acceptance_criteria(raw: str) -> list[AcceptanceCriterion]:
    """Parse raw acceptance criteria text into numbered AcceptanceCriterion items.

    Handles:
    - Bulleted lists (-, *, •)
    - Numbered lists (1., 2., 1), 2))
    - Plain prose separated by blank lines
    - Plain prose separated by single newlines

    Returns [] on empty input or any exception.
    """
    try:
        if not raw or not raw.strip():
            return []

        # Split on bullet or numbered list markers at the start of a line,
        # or on double-newlines (paragraph breaks).
        lines = re.split(_AC_SPLIT_RE, raw)

        # If that produced only one non-empty chunk -- or if any single chunk
        # STILL carries more than one `ACn:` label, which is exactly what a
        # trailing paragraph used to hide -- fall back to single-newline split.
        non_empty = [ln.strip() for ln in lines if ln.strip()]
        if len(non_empty) <= 1 or any(
            len(_AC_LABEL_RE.findall(ln)) > 1 for ln in non_empty
        ):
            lines = raw.splitlines()

        # 2026-08-31 (F3): a Business-Rules table flattens to ONE line carrying
        # every `BRnn:` label, so neither split above separates anything. Split
        # on the label itself -- only where at least two are present.
        expanded: list[str] = []
        for _ln in lines:
            _text = _ln if isinstance(_ln, str) else ""
            if len(_ANY_LABEL_RE.findall(_text)) >= _MIN_INLINE_LABELS:
                expanded += [
                    p for p in _INLINE_LABEL_SPLIT_RE.split(_text) if p.strip()
                ]
            else:
                expanded.append(_text)
        lines = expanded

        items: list[str] = []
        for line in lines:
            line = line.strip()
            # Strip any residual leading list marker that survived the split.
            # A bare digit is CONTENT (e.g. "3 failed logins", "200ms"); only
            # strip a leading number when it is a real list marker — i.e. it is
            # immediately followed by a delimiter (./)/]) AND whitespace.
            line = re.sub(
                r"^\s*(?:[-*•]|\d+[.)\]]|AC\s*-?\s*\d{1,3}\s*[:.)\]-])\s+",
                "",
                line,
            ).strip()
            if len(line) < 5:
                continue
            # A trailing "Notes:"/"Links:" paragraph is prose ABOUT the ticket,
            # not a criterion -- see _NON_AC_PREFIX_RE.
            if _BARE_SECTION_RE.match(line):
                logger.debug("Dropping bare section heading: %.60r", line)
                continue
            if _NON_AC_PREFIX_RE.match(line):
                logger.debug("Dropping non-criterion section label: %.60r", line)
                continue
            # A configured AC field can return something that is not a
            # requirement at all (a date, an id, a number). Letting it
            # through creates a bogus AC that every downstream anchoring
            # check then treats as ground truth.
            if not looks_like_requirement_text(line):
                logger.debug("Dropping non-requirement AC candidate: %.60r", line)
                continue
            items.append(line)

        if not items:
            return []

        return [
            AcceptanceCriterion(ac_id=f"AC-{i:03d}", description=desc)
            for i, desc in enumerate(items, 1)
        ]
    except Exception:
        logger.exception("parse_acceptance_criteria failed — returning empty list")
        return []


def _norm_label(raw: object) -> str:
    """Canonicalise a non-AC criterion label (BR07, br-7, MSG01) or "".

    Mirrors ``normalize_ac_id``'s zero-padding for any short uppercase prefix,
    so "BR-7" and "BR07" compare equal. Returns "" for anything that is not
    label-shaped, which keeps free text out of the trace index. Never raises.
    """
    try:
        s = str(raw or "").strip().upper().replace(" ", "")
        m = re.match(r"^([A-Z]{2,5})[-_]?0*(\d{1,3})$", s)
        return f"{m.group(1)}-{int(m.group(2)):03d}" if m else ""
    except Exception:
        return ""


def _leading_label(text: object) -> str:
    """The normalised label a criterion's own text starts with, or "".

    ``BR07: System shall ...`` -> ``BR-007``. Never raises.
    """
    try:
        m = _ANY_LABEL_RE.match(str(text or "").strip())
        return _norm_label(re.sub(r"[\s:.)\]-]+$", "", m.group(0))) if m else ""
    except Exception:
        return ""


def _trace_map(
    acs: list[AcceptanceCriterion], test_cases: list[TestCase]
) -> tuple[dict, list]:
    """Map each AC id -> the tc_ids citing it, plus the cases citing nothing.

    Extracted so build_rtm_summary, rtm_trace and traceability_warning_section all
    read ONE computation instead of three traversals that could disagree. Match on
    the *normalized* id so a case tagged "AC-1"/"ac001" still traces to canonical
    "AC-001".
    """
    ac_to_tcs: dict[str, list[str]] = {ac.ac_id: [] for ac in acs}
    norm_to_canonical: dict[str, str] = {
        normalize_ac_id(ac.ac_id): ac.ac_id for ac in acs
    }
    # 2026-08-31 (F4): a criterion parsed out of `BR07: ...` keeps its own label
    # in the text, and a generator asked to cite "the acceptance criterion this
    # verifies" cites THAT label, not the synthetic AC-00n id it has never been
    # shown. Both indexes are consulted, so `requirement_id: "BR07"` traces --
    # instead of the workbook printing BR07 in the Requirement ID column and
    # declaring, two sheets later, that no case carries a usable one.
    for _ac in acs:
        _label = _leading_label(getattr(_ac, "description", ""))
        if _label:
            norm_to_canonical.setdefault(_label, _ac.ac_id)
    orphan_tc_ids: list[str] = []
    for tc in test_cases:
        canonical = norm_to_canonical.get(
            normalize_ac_id(tc.requirement_id)
        ) or norm_to_canonical.get(_norm_label(tc.requirement_id))
        if canonical:
            ac_to_tcs[canonical].append(tc.tc_id)
        else:
            orphan_tc_ids.append(tc.tc_id)
    return ac_to_tcs, orphan_tc_ids


def rtm_trace(acs: list, test_cases: list) -> dict:
    """The traceability outcome as DATA, for the audit trail.

    build_rtm_summary has always PRINTED these numbers; nothing carried them out,
    so "is traceability degenerate?" needed a hand investigation. Never raises --
    an unreadable suite yields zeros rather than breaking a generation.
    """
    try:
        if not acs:
            return {"acs": 0, "covered": 0, "traced_cases": 0, "orphan_cases": 0}
        ac_to_tcs, orphan_tc_ids = _trace_map(acs, test_cases)
        return {
            "acs": len(acs),
            "covered": sum(1 for tcs in ac_to_tcs.values() if tcs),
            "traced_cases": sum(len(tcs) for tcs in ac_to_tcs.values()),
            "orphan_cases": len(orphan_tc_ids),
        }
    except Exception:
        logger.exception("rtm_trace failed -- returning zeros")
        return {"acs": 0, "covered": 0, "traced_cases": 0, "orphan_cases": 0}


def orphan_case_ids(acs: list, test_cases: list, *, cap: int = 20) -> list:
    """The tc_ids of the cases that trace to NO acceptance criterion.

    rtm_trace already COUNTS them; the submit-side nudge needs to NAME a few, and
    rtm_trace's dict is asserted byte-for-byte by tests/test_rtm.py, so this is a
    second reader of the SAME _trace_map computation rather than a new key on a
    contract other code already depends on. Order is the suite's own; the list is
    capped because it is rendered into a tester-facing note.

    Never raises -- an unreadable suite yields [] rather than breaking a
    generation, exactly like rtm_trace beside it.
    """
    try:
        if not acs or not test_cases:
            return []
        _ac_to_tcs, orphan_tc_ids = _trace_map(acs, test_cases)
        try:
            limit = max(0, int(cap))
        except (TypeError, ValueError):
            limit = 20
        return [str(t) for t in orphan_tc_ids][:limit]
    except Exception:
        logger.exception("orphan_case_ids failed -- returning []")
        return []


def traceability_warning_section(acs: list, test_cases: list) -> str:
    """Escalate a DEGENERATE traceability outcome from a percentage to a finding.

    build_rtm_summary already prints "Coverage: 1 of 7 ACs covered (14%)". On the
    2026-07-29 and 2026-07-30 runs it did exactly that and nobody read it -- a
    percentage reads as a metric, not as a defect. This names it.

    Fires when more than one AC exists but at most ONE of them is cited --
    and also when exactly ONE AC was parsed yet most cases trace to nothing
    (the misparsed-AC-source signature, e.g. a date-valued JIRA_AC_FIELD).
    ``covered_count <= 1``, not ``== 1``: zero is strictly WORSE and is silent
    under an equality test -- and it has happened, when cases were tagged with
    checklist ids instead of AC ids.

    Counts are REAL, never "all N cases": a case citing nothing lands in
    orphan_tc_ids, so 1 traced case plus 64 orphans must not be reported as 65
    cases tracing to one AC. FLAG ONLY -- nothing is dropped or rewritten. States
    an observation, not an accusation: a legitimately small suite cannot cover 7
    ACs. Never raises.
    """
    try:
        if not acs or not test_cases:
            return ""
        ac_to_tcs, orphan_tc_ids = _trace_map(acs, test_cases)
        total = len(acs)
        covered = sum(1 for tcs in ac_to_tcs.values() if tcs)
        if total == 1:
            # The lone-AC + orphan-majority signature: on 2026-08-03 (run
            # f9094582) a DATE-valued custom field was parsed as the only
            # "AC", 61/98 cases traced to nothing, and this advisory stayed
            # silent behind `total <= 1` while the RTM read as covered.
            share = len(orphan_tc_ids) / len(test_cases)
            if share <= 0.5:
                return ""
            only_id = next(iter(ac_to_tcs), "AC-001")
            return (
                "\n\n> \u26a0\ufe0f  **Requirement traceability looks degenerate.** "
                f"Only ONE acceptance criterion (`{only_id}`) was parsed from "
                f"the source, and {len(orphan_tc_ids)} of {len(test_cases)} "
                "case(s) trace to nothing. A single AC with an orphan majority "
                "usually means the AC source field is misconfigured (for "
                "example `JIRA_AC_FIELD` pointing at a non-AC custom field), "
                "so the RTM cannot tell you which requirements are actually "
                "tested. Verify the AC field before trusting this suite's "
                "coverage numbers."
            )
        if total <= 1 or covered > 1:
            return ""
        head = "\n\n> \u26a0\ufe0f  **Requirement traceability looks degenerate.** "
        if covered == 0:
            body = (
                f"No test case traces to any of the {total} acceptance criteria "
                f"({len(orphan_tc_ids)} case(s) carry no usable `requirement_id`)."
            )
        else:
            cited = next((ac_id for ac_id, tcs in ac_to_tcs.items() if tcs), "")
            traced = sum(len(tcs) for tcs in ac_to_tcs.values())
            body = (
                f"{traced} case(s) trace to `{cited}` and {len(orphan_tc_ids)} "
                f"trace to nothing, out of {total} acceptance criteria "
                f"({total - covered} never referenced)."
            )
        return (
            head
            + body
            + " Traceability is unreliable for this suite: the RTM above cannot "
            "tell you which requirements are actually tested. Re-check the "
            "`requirement_id` on each case against the AC list."
        )
    except Exception:
        logger.exception("traceability_warning_section failed -- returning empty")
        return ""


def build_rtm_summary(
    acs: list[AcceptanceCriterion], test_cases: list[TestCase]
) -> str:
    """Build a markdown RTM coverage table and coverage stats.

    Returns empty string when acs is empty (no traceability data available).
    """
    if not acs:
        return ""

    ac_to_tcs, orphan_tc_ids = _trace_map(acs, test_cases)
    covered_count = sum(1 for tcs in ac_to_tcs.values() if tcs)
    total_count = len(acs)
    pct = int(covered_count / total_count * 100) if total_count else 0

    # Build table rows
    rows: list[str] = []
    for ac in acs:
        linked = ac_to_tcs[ac.ac_id]
        linked_str = ", ".join(linked) if linked else ""
        status = "Covered" if linked else "ORPHAN"
        desc = (
            ac.description[:80] + "..." if len(ac.description) > 80 else ac.description
        )
        rows.append(f"| {ac.ac_id} | {desc} | {linked_str} | {status} |")

    table = (
        "\n\n---\n\n"
        "## Requirements Traceability Matrix\n\n"
        "| AC ID | Acceptance Criterion | Linked TCs | Status |\n"
        "|-------|----------------------|------------|--------|\n" + "\n".join(rows)
    )

    coverage_line = (
        f"\n\n**Coverage: {covered_count} of {total_count} ACs covered ({pct}%)."
    )
    if total_count - covered_count > 0:
        coverage_line += f" {total_count - covered_count} orphan AC(s) flagged.**"
    else:
        coverage_line += " All ACs covered.**"

    orphan_tc_line = ""
    if orphan_tc_ids:
        orphan_tc_line = (
            "\n\n**Orphan test cases (no linked requirement): "
            + ", ".join(orphan_tc_ids[:20])
            + (" ..." if len(orphan_tc_ids) > 20 else "")
            + "**"
        )

    return table + coverage_line + orphan_tc_line


# Bounds for the traceability SHEET. The description is a spreadsheet cell, not
# the 80-char markdown one build_rtm_summary trims to, so it can be generous --
# but a host-authored AC has no length contract at all. The linked-TC cap keeps
# one over-weighted criterion (25 of 96 cases on AC-001 in the 2026-08-16 run)
# from producing a cell no spreadsheet will render.
_RTM_DESC_CAP = 1000
_RTM_LINKED_CAP = 60

# D1 (2026-08-21). The Status cell for a criterion nothing links to, WHEN the
# whole suite carries no usable `requirement_id`. "ORPHAN" asserts that this
# criterion was missed; on the 2026-08-21 SHYJ-5646 run that assertion was made
# 4 times about a suite whose 96 cases plainly exercised the criteria and simply
# arrived with the link field null. ASCII "--" to match this file's style.
_NOT_REPORTED = "NOT REPORTED -- the generator returned no requirement links"


def _linked_cell(tc_ids: list) -> str:
    """A capped, comma-joined list of linked tc_ids, with the shortfall NAMED."""
    shown = ", ".join(str(t) for t in tc_ids[:_RTM_LINKED_CAP])
    extra = len(tc_ids) - _RTM_LINKED_CAP
    return f"{shown}, ... (+{extra} more)" if extra > 0 else shown


def rtm_rows(acs: list, test_cases: list, *, derived: bool = False) -> list:
    """Rows (header first) for the 'Requirements Traceability' XLSX sheet.

    F06 (2026-08-19). The finalize reply's headline -- "7/7 acceptance criteria
    traced, all covered" -- was unverifiable by the person who RECEIVES the
    workbook: ``requirement_id`` sat on every case in the database and was dropped
    on the way to the spreadsheet. These rows are the SAME ``_trace_map``
    computation the reply prints, shaped for a sheet, so the file and the claim
    cannot drift apart.

    The per-AC CASE COUNT is a column of its own on purpose: a suite can be 7/7
    covered and still be 25 cases on one criterion against 3 on another, and a
    total hides exactly that.

    *derived* means the criteria were SYNTHESIZED because the source carried none
    (``rtm_oneline`` says the same thing on the reply). 100% coverage of invented
    requirements is not evidence of anything, so the caveat travels with the file.

    Pure -- cell sanitisation happens in tools/xlsx_generator, exactly like
    ``atomic_checklist.checklist_rows``. Returns [] when there are no criteria, so
    no sheet is written at all. Never raises."""
    try:
        if not acs:
            return []
        cases = list(test_cases or [])
        ac_to_tcs, orphan_tc_ids = _trace_map(acs, cases)
        covered = sum(1 for tcs in ac_to_tcs.values() if tcs)
        # D1 (2026-08-21). Two DIFFERENT outcomes were printed identically.
        #
        # 2026-08-16, SHYJ-5645: 40 of 64 cases linked, 24 did not, 3 criteria
        # were never referenced. Those 3 are ORPHAN and "x of y covered (n%)" is
        # a true statement about a suite that really does miss them.
        #
        # 2026-08-21, SHYJ-5646: the host returned `requirement_id: null` on all
        # 96 cases, and this sheet said "0 of 4 acceptance criteria covered (0%)"
        # with four ORPHAN rows and no caveat. A tester reads that as a coverage
        # FAILURE by the suite. It is not -- the cases plainly exercise the
        # criteria; only the LINK FIELD is absent. The same workbook's
        # 'Coverage Audit' sheet, driven by the same missing data, prints
        # "SUPPRESSED -- lexical fallback" rather than a number, so one file
        # carried two coverage reports, one hedged and one not.
        #
        # So: when NOT ONE case carries a usable `requirement_id`, this sheet
        # reports the data as ABSENT rather than the coverage as zero. The
        # genuine-orphan render above is unchanged, byte for byte.
        #
        # `bool(cases)` matters: an EMPTY suite is not evidence of a broken
        # generator, and it must keep today's wording.
        no_links = bool(cases) and covered == 0 and len(orphan_tc_ids) == len(cases)
        status_uncovered = _NOT_REPORTED if no_links else "ORPHAN"
        rows: list = [
            ["AC ID", "Acceptance Criterion", "Cases", "Linked TCs", "Status"]
        ]
        for ac in acs:
            linked = ac_to_tcs.get(ac.ac_id) or []
            rows.append(
                [
                    ac.ac_id,
                    str(getattr(ac, "description", "") or "")[:_RTM_DESC_CAP],
                    str(len(linked)),
                    _linked_cell(linked),
                    "Covered" if linked else status_uncovered,
                ]
            )
        if orphan_tc_ids:
            # F04 will make an untraced case legitimate; this row already renders
            # one honestly rather than as an absence a reader has to notice.
            #
            # D1: that sentence points at "the two case numbers on the coverage
            # line below". In the no-links shape the coverage line below carries
            # NO pair of case numbers, so the pointer would point at nothing.
            # Say what is true in that shape instead of leaving a dangling
            # cross-reference in the deliverable.
            untraced_desc = (
                "EVERY case in this suite is listed here: not one carries a "
                "usable `requirement_id`, so this sheet cannot say which "
                "requirement any of them tests. They are UNTRACED, which is not "
                "the same as untested."
                if no_links
                else "Cases carrying no `requirement_id`. They test something, but "
                "this sheet cannot say which requirement -- they are exactly "
                "the gap between the two case numbers on the coverage line "
                "below, and they raise no criterion's count."
            )
            rows.append(
                [
                    "(untraced)",
                    untraced_desc,
                    str(len(orphan_tc_ids)),
                    _linked_cell(orphan_tc_ids),
                    "NOT TRACED",
                ]
            )
        total = len(acs)
        traced = sum(len(tcs) for tcs in ac_to_tcs.values())
        pct = int(covered / total * 100) if total else 0
        kind = "MODEL-DERIVED acceptance criteria" if derived else "acceptance criteria"
        rows.append(["", "", "", "", ""])
        if no_links:
            # The percentage is SUPPRESSED, not rendered as 0%: 0% is a claim
            # about the suite, and the only thing actually known is that the
            # generator returned no links. The criteria rows are KEPT -- the
            # reader still needs to see what was meant to be covered.
            rows.append(
                [
                    "Coverage",
                    f"NOT REPORTED -- none of the {len(cases)} case(s) carries a "
                    f"usable `requirement_id`, so coverage of the {total} {kind} "
                    "cannot be measured from this suite. The percentage is "
                    "SUPPRESSED rather than reported as 0%: these cases are "
                    "untraced, NOT untested. Re-check the `requirement_id` on "
                    "each case against the criteria above.",
                    "",
                    "",
                    "",
                ]
            )
        else:
            rows.append(
                [
                    "Coverage",
                    f"{covered} of {total} {kind} covered ({pct}%) -- "
                    f"{traced} of {len(cases)} case(s) trace to one.",
                    "",
                    "",
                    "",
                ]
            )
        if derived:
            rows.append(
                [
                    "Provenance",
                    "These criteria were SYNTHESIZED because the source carried "
                    "none, so this table measures self-consistency, NOT coverage "
                    "of stated requirements.",
                    "",
                    "",
                    "",
                ]
            )
        return rows
    except Exception:
        logger.exception("rtm_rows failed -- returning []")
        return []


# C1/C2 (SHYJ-5138, 2026-08-21). One delivered workbook carried two coverage
# claims that contradicted each other. 'Requirements Traceability' ended with
# "4 of 4 acceptance criteria covered (100%) -- 56 of 64 case(s) trace to one."
# while 'Coverage Audit', on the same run, reported "Requirements traced 6" of
# 15 with EVERY percentage "SUPPRESSED -- lexical fallback" and its 9 gaps and
# 56 orphans self-labelled UNRELIABLE. A tester reads the 100%.
#
# They are NOT two measurements of one thing, and this must not pretend they
# are:
#   * the traceability figure is a DECLARED-LINK tally over the COARSE
#     acceptance criteria parsed from the source (4 of them). It is
#     deterministic and true in its own terms -- and it says nothing about
#     whether a case actually verifies the criterion its `requirement_id` names.
#   * the audit figure is a SIMILARITY MATCH over the FINER EARS atomic
#     checklist (15 items), and on the lexical fallback it is wrong in BOTH
#     directions, which is why that sheet suppresses its own percentages.
#
# So the traceability percentage is NOT suppressed here. Suppressing a
# deterministic tally because an unrelated matcher is degraded would make the
# honest tier look like the dishonest one, and a guard that fires on a number it
# does not govern is exactly how a gate goes inert. Instead the cell names its
# own SCOPE, and one added row states the other sheet's figure, its different
# denominator, its tier, and -- only when that matcher really is degraded --
# that it cannot be trusted and what to set.
#
# Applied at RENDER time (tools/xlsx_generator._write_rtm_sheet) rather than
# inside `rtm_rows`: rtm_rows runs at finalize with no knowledge of the matcher
# tier, and the workbook is the only place that holds both artifacts. That also
# keeps rtm_rows' output byte-identical, so the D1 exact-equality contract in
# tests/test_f06_requirement_export.py stands and this fix has to be tested
# against the DELIVERED cell rather than a builder return.
_SCOPE_NOTE = (
    " SCOPE: this counts DECLARED `requirement_id` links against the acceptance"
    " criteria parsed from the source. It does NOT check that a case verifies"
    " the criterion it names, and it is a COARSER requirement set than the"
    " 'Coverage Audit' sheet's atomic checklist -- the two sheets are not"
    " measuring the same thing, and neither number alone establishes coverage."
)


def reconcile_coverage_rows(rows: list, coverage: dict | None) -> list:
    """Amend traceability rows with their own scope and the audit's figure.

    *rows* is ``rtm_rows`` output; *coverage* is the checklist coverage dict
    (``coverage_to_dict`` above) or None. Returns a NEW row list and never
    mutates the input. Never raises -- on any surprise the rows come back
    exactly as they went in, because a reconciliation note must never cost the
    workbook its traceability sheet.

    Three guards, all load-bearing:
      * no ``Coverage`` row -> nothing to qualify, rows unchanged;
      * a Coverage cell already reading "NOT REPORTED --" (the D1 no-links
        shape) is left ALONE: it publishes no percentage, so it has no scope to
        qualify and no contradiction to reconcile;
      * the audit row is emitted only when the coverage object carries a real
        denominator -- which is the PRESENTED count (``presented_items``,
        falling back to ``total_items``), the same denominator the 'Coverage
        Audit' sheet's "Coverage % (of presented)" row uses, which is why the
        wording says "of the N presented": on a prompt-capped run the two differ
        and "of N" would overstate the requirement set the generator ever saw; and the UNRELIABLE / QA_EMBEDDINGS_BACKEND wording only
        when that object says ``degraded``.
    """
    try:
        out = [list(r) for r in rows]
        idx = -1
        for i, row in enumerate(out):
            if row and str(row[0]) == "Coverage":
                idx = i
                break
        if idx < 0:
            return out
        cell = str(out[idx][1]) if len(out[idx]) > 1 else ""
        if cell.startswith("NOT REPORTED --"):
            return out
        out[idx][1] = cell + _SCOPE_NOTE
        cov = coverage or {}
        total = int(cov.get("presented_items") or cov.get("total_items") or 0)
        if total <= 0:
            return out
        traced = len(cov.get("covered_item_ids") or [])
        tier = str(cov.get("tier_used") or "unknown")
        head = (
            "The 'Coverage Audit' sheet measures a DIFFERENT and FINER "
            f"requirement set -- {traced} of the {total} presented atomic "
            "checklist requirements traced"
        )
        if cov.get("degraded"):
            tail = (
                " -- by SIMILARITY MATCHING rather than by declared links. That "
                f"matcher ran on the LEXICAL fallback (matcher tier: {tier}) "
                "because no embeddings backend is configured, so every "
                "percentage on that sheet is SUPPRESSED and its gap and orphan "
                "lists are labelled UNRELIABLE -- wrong in BOTH directions, not "
                "merely pessimistic. So read the percentage above as what it "
                "is, a link tally over the coarser criteria; it is NOT a second "
                "opinion confirming this suite's coverage. Set "
                "QA_EMBEDDINGS_BACKEND for a trustworthy second measurement."
            )
        else:
            pct = float(cov.get("coverage_pct") or 0.0)
            tail = (
                f" ({pct:.1f}%) -- by similarity matching on the {tier} tier "
                "rather than by declared links. Read both: the percentage above "
                "is a link tally over the coarser criteria, this one is a match "
                "over the finer requirement set."
            )
        out.insert(idx + 1, ["Second measurement", head + tail, "", "", ""])
        return out
    except Exception:
        logger.exception("reconcile_coverage_rows failed -- rows unchanged")
        return list(rows or [])


def rtm_oneline(
    acs: list[AcceptanceCriterion],
    test_cases: list[TestCase],
    derived: bool = False,
) -> str:
    """Return a single-line RTM coverage stat (no table) for compact summaries.

    Returns empty string when acs is empty. Never raises.

    ``derived=True`` means the criteria were NOT read from the ticket -- the host's
    chat model synthesized them as scaffolding. 2026-08-03: a real run finalized
    with this line reading "6/6 acceptance criteria traced, all covered" against
    six criteria the model had invented, because the ticket carried none. The
    honest disclosure did exist, but in a separate block ABOVE; this line sits in
    the headline stats next to Risk, and on its own it reads as verified
    traceability. 100% coverage of invented requirements is not evidence of
    anything, so the provenance has to travel WITH the number rather than near it.
    Defaults False, so a ticket that really carried criteria is unchanged.

    F04 (2026-08-16) applied the same reasoning to a second missing number. The
    line reports TWO figures now: how many criteria have at least one case, and
    how many CASES trace to a criterion. A case whose ``requirement_id`` is null
    -- or names an id this AC list does not contain -- counts as untraced, which
    is why the second figure can fall short of the suite size while the first
    still reads "all covered".
    """
    try:
        if not acs:
            return ""
        # 2026-08-31 (F4): this used to re-derive its own id match from
        # normalize_ac_id, which is the ONE thing _trace_map's docstring says it
        # exists to prevent. The two then disagreed inside a single reply: the
        # orphan line said 19 of 66 cases were untraced and this line, four
        # lines above it, said 66 of 66 were -- because only _trace_map had
        # learned to match a case tagged `BR07` to the criterion that IS BR07.
        ac_to_tcs, orphan_tc_ids = _trace_map(list(acs), list(test_cases or []))
        covered = sum(1 for tcs in ac_to_tcs.values() if tcs)
        total = len(acs)
        orphans = total - covered
        kind = "MODEL-DERIVED acceptance criteria" if derived else "acceptance criteria"
        line = f"\n\n**Requirements:** {covered}/{total} {kind} traced"
        line += f", {orphans} orphan(s)." if orphans else ", all covered."
        # F04 (2026-08-16): the AC figure ALONE read "7/7 acceptance criteria
        # traced, all covered" over a suite whose deterministic checklist matcher
        # mapped 21 of its 96 cases. The two figures answer different questions —
        # how many criteria got at least one case, versus how many cases verify a
        # stated criterion — and printing only the first is what let a suite of
        # convenience tags read as fully traced. They travel together from here,
        # so "all covered" can never stand alone. Counts only (the clause grows
        # with integer WIDTH, never with the suite), and silent when there are no
        # cases at all, which keeps the stored-suite re-render byte-identical.
        cases = list(test_cases or [])
        if cases:
            untraced = len(orphan_tc_ids)
            traced = len(cases) - untraced
            line += f" {traced} of {len(cases)} case(s) trace to one of them"
            line += f"; {untraced} trace to none." if untraced else "."
        if derived:
            line += (
                " They were synthesized because the ticket carried none, so this "
                "measures self-consistency, NOT coverage of stated requirements."
            )
        return line
    except Exception:  # pragma: no cover - defensive, never break the summary
        logger.exception("rtm_oneline failed — returning empty string")
        return ""


def format_ac_prompt_block(acs: list[AcceptanceCriterion]) -> str:
    """Format ACs into a system-prompt block for LLM instruction.

    Returns empty string when acs is empty.
    """
    if not acs:
        return ""

    lines = "\n".join(f"- {ac.ac_id}: {ac.description}" for ac in acs)
    return (
        "\n\n## Acceptance Criteria (populate requirement_id)\n"
        "For each test case, set `requirement_id` to the ID of the AC it primarily validates.\n"
        "Use ONLY these AC IDs:\n"
        + lines
        + (
            # F04 (live run 2026-08-16, suite 1ed83399b4b84831b79ead7936235989).
            # The clause that stood here offered null and then argued against it
            # in the same breath ("but prefer a real AC id ... usually testing
            # something outside this ticket's scope"), so the model picked the
            # nearest id instead of declaring itself untraced: 96 of 96 cases
            # tagged, 0 untraced, and AC-001 — the first and broadest id —
            # absorbing 25 of them while its sibling AC-002 got 3. Roughly 20 of
            # those tags were tags of convenience (authorisation, rate limiting,
            # RTL layout, screen-reader labels), which no AC on that ticket
            # stated, and they inflated the reply's "7/7 ... all covered".
            # A null is now stated to be CORRECT rather than tolerated, the
            # shapes that legitimately have no AC are named, and the consequence
            # of stretching a tag is named too — rtm_oneline reports the
            # case-level count unconditionally, so a stretched tag is not a
            # private convenience, it moves a number every reader sees.
            "\nIf none of the IDs above applies to a test case, set "
            "requirement_id to JSON null. That is the CORRECT answer, not a "
            "gap: security, accessibility, empty-state and cross-device cases "
            "routinely verify something no acceptance criterion states, and "
            "they are legitimate tests. Do NOT stretch an ID to cover a case it "
            "does not literally state. Every case is counted either way — the "
            "summary reports how many trace to a criterion and how many trace "
            "to none — so a stretched tag does not hide anything, it only "
            "overstates coverage for everyone downstream.\n"
        )
    )


# --------------------------------------------------------------------------- #
# Bidirectional checklist matcher (Batch 2, Pass 3)
#
# WHY THIS EXISTS: build_rtm_summary above matches TC -> AC via
# ``tc.requirement_id``, which the GENERATING model self-assigns — it marks its
# own homework, and normalize_ac_id only fuzzy-repairs that self-tagging. The
# matcher below is EXTERNAL and DETERMINISTIC: it compares each requirement's
# text against each case's title + expected results, and never reads
# ``requirement_id`` at all.
#
# Three tiers, cheapest first:
#   (a) embedding cosine via tools/embeddings (TF-IDF lexical fallback, pure
#       stdlib, when the backend is disabled or fails);
#   (b) two OPTIONAL batched LLM tiers -- entailment over the ambiguous middle
#       band, then adjudication over what (a)+(b) could not separate -- were
#       DELETED on 2026-08-16 (dead-code deletion P2-G1). They were the last
#       two `llm.ask_json` calls in the tree. Both had been unreachable since
#       2026-08-14 (batch 8b-ii) behind `_nli_tier_enabled()` /
#       `_adjudicate_tier_enabled()`, which survive below as `False` constants
#       and still gate the degradation note. The ambiguous band itself is gone
#       with them, so a score below the HIGH threshold is simply not a link.
#       Reviving the tiers is now a fresh implementation, not a flag flip;
#       `git show <this commit>~1` is the last tree that carried them.
#
# THREE CORRECTNESS INVARIANTS, each of which was a real defect risk:
#   1. TRUNCATION IS NOT A GAP. ``presented_item_ids`` names the items that
#      actually reached the generator. Anything else goes to
#      ``not_presented_item_ids`` and is EXCLUDED from coverage_pct / gap_rate.
#   2. A DEGRADED RUN PUBLISHES NO PERCENTAGE. Lexical TF-IDF cosine is not on
#      the same scale as embedding cosine, so ``checklist_tally_line`` prints
#      "UNRELIABLE (lexical fallback)" with the percentage suppressed instead of
#      a bold number nobody should trust.
#   3. THE MATRIX NEVER RUNS ON THE EVENT LOOP. items x cases pure-Python
#      cosines (up to 200 x 80) are built inside ``asyncio.to_thread`` so the
#      MCP stdio loop keeps serving.
# --------------------------------------------------------------------------- #

# Lexical TF-IDF cosine lives on a different scale than embedding cosine, so the
# operator-tunable embedding thresholds must NOT be applied to it. These fixed
# lexical thresholds are used instead, a lexical match is capped at MEDIUM
# confidence (never HIGH), and the tally suppresses the percentage entirely.
#
# MEASURED 2026-08-19 (finding F10), over the 96 persisted cases and 20
# checklist items of suite 1ed83399b4b84831b79ead7936235989. All SEVEN reported
# gaps were FALSE: for each one the top-ranked lexical hit was the plainly
# correct case (CL-011 -> TC-044 at 0.420, CL-013 -> TC-046 at 0.442,
# CL-020 -> TC-005 at 0.441, CL-004 -> TC-010 at 0.177). True coverage was
# 20/20. That is NOT an argument for lowering this number: the same sweep gives
# 17/20 items and 29/96 cases at 0.40, and 18/20 and 48/96 at 0.30 -- no cut-off
# recovers coverage without collapsing the orphan signal, and two requirements
# stay uncovered all the way down. The tier cannot produce a trustworthy figure
# at ANY threshold, which is exactly why the tally suppresses the percentage.
# Do not tune this constant in response to a low lexical coverage report.
_LEXICAL_HIGH = 0.45

# Above this many (items x cases) cells the matrix build is slow enough to be
# worth telling the operator about. It is NOT a drop threshold — dropping pairs
# would silently understate coverage; the work is simply offloaded to a thread.
_MATRIX_CELL_WARN = 20000

_DEGRADED_NOTE = (
    "No embeddings backend was available, so requirement matching used the "
    "pure-lexical TF-IDF fallback. TF-IDF cosine between an EARS requirement and "
    "a test case rarely clears the match threshold for a genuine paraphrase, so "
    "the numbers below UNDERSTATE coverage and NO percentage is reported. Set "
    "QA_EMBEDDINGS_BACKEND (local or voyage) and re-run for a usable audit."
)


@dataclass
class MatchLink:
    """One requirement -> test-case link produced by the external matcher."""

    item_id: str
    tc_id: str
    score: float
    confidence: str  # HIGH | MEDIUM | LOW
    tier: str  # embeddings | lexical | entailment | adjudication


@dataclass
class ChecklistCoverage:
    """Bidirectional coverage result. Never carries an exception.

    ``total_items`` is the WHOLE checklist; ``presented_items`` is how many of
    them reached the generator. coverage_pct / gap_rate are computed over
    ``presented_items`` only — see ``not_presented_item_ids``."""

    total_items: int = 0
    presented_items: int = 0
    total_cases: int = 0
    links: list = dc_field(default_factory=list)
    covered_item_ids: list = dc_field(default_factory=list)
    gap_item_ids: list = dc_field(default_factory=list)
    not_presented_item_ids: list = dc_field(default_factory=list)
    orphan_tc_ids: list = dc_field(default_factory=list)
    confidence_counts: dict = dc_field(default_factory=dict)
    coverage_pct: float = 0.0
    gap_rate: float = 0.0
    orphan_rate: float = 0.0
    tier_used: str = ""
    degraded: bool = False
    notes: list = dc_field(default_factory=list)
    ran: bool = False


def _item_text(item) -> str:
    return f"{getattr(item, 'text', '') or ''}".strip()


def _case_match_payload(tc: TestCase) -> str:
    """What the matcher reads for a case: title + every expected result.

    The expected results are the verifiable claims — matching on step ACTIONS
    would reward navigation boilerplate shared by every case. Bounded so a
    pathological case cannot blow up the embedding payload."""
    try:
        expected = " ".join((s.expected_result or "").strip() for s in (tc.steps or []))
        return f"{(tc.title or '').strip()} || {expected}".strip()[:1200]
    except Exception:
        return (getattr(tc, "title", "") or "").strip()[:1200]


def _cosine_matrix_sync(item_vectors: list, case_vectors: list) -> list:
    """Pure-Python O(items x cases) cosine matrix. ALWAYS called through
    ``asyncio.to_thread`` — 200 x 80 x 384-dim on the event loop would stall the
    MCP stdio server for about a second, and the checklist-remediation loop can
    call the matcher once per round."""
    return [[cosine_similarity(a, b) for b in case_vectors] for a in item_vectors]


async def _similarity_matrix(item_texts: list[str], case_texts: list[str]) -> tuple:
    """(matrix, tier, degraded). Tier (a). Never raises.

    ONE batched embed_texts call covering both sides, then the cosine matrix
    built OFF the event loop. Falls back to the stdlib TF-IDF matrix (also off
    the event loop) whenever embeddings are disabled, error out, or return a
    mismatched vector count."""
    cells = len(item_texts) * len(case_texts)
    if cells > _MATRIX_CELL_WARN:
        logger.info(
            "checklist matcher: building a %d x %d similarity matrix (%d cells) "
            "in a worker thread",
            len(item_texts),
            len(case_texts),
            cells,
        )
    try:
        if backend_enabled():
            emb = await embed_texts(list(item_texts) + list(case_texts))
            vectors = emb.get("content") if isinstance(emb, dict) else None
            if (
                not emb.get("error")
                and vectors
                and len(vectors) == len(item_texts) + len(case_texts)
            ):
                iv = vectors[: len(item_texts)]
                cv = vectors[len(item_texts) :]
                matrix = await asyncio.to_thread(_cosine_matrix_sync, iv, cv)
                return matrix, "embeddings", False
            logger.info(
                "checklist matcher: embeddings unavailable (%s) — using the "
                "lexical TF-IDF fallback",
                (emb or {}).get("error") if isinstance(emb, dict) else "no content",
            )
    except Exception:
        logger.exception("checklist matcher: embedding tier failed — going lexical")
    try:
        matrix = await asyncio.to_thread(
            lexical_cosine_matrix, list(item_texts), list(case_texts)
        )
    except Exception:
        logger.exception("checklist matcher: lexical tier failed — scoring zeros")
        matrix = [[0.0] * len(case_texts) for _ in item_texts]
    return matrix, "lexical", True


def _nli_tier_enabled() -> bool:
    """Checklist entailment (tier b) is OFF, and since 2026-08-16 it is GONE.

    NOT settings-derived: QA_CHECKLIST_NLI_ENABLED was DELETED (flag-surface
    reduction, batch 8b-ii) and hardcoded OFF -- its own code default and the
    value .env.example shipped, so no install changed. Dead-code deletion
    P2-G1 then deleted `_entailment_pass` itself, along with the ambiguous
    band that fed it.

    The seam is RETAINED as documentation and as the switch that still gates
    the degradation note in match_checklist -- the only channel by which a
    revived tier's weakened measurement reaches the EXPORTED artifact rather
    than just the chat reply. It no longer gates a call, so flipping it now
    changes nothing except that note: reviving the tier means writing the pass,
    the band and its prompt again.
    """
    return False


def _adjudicate_tier_enabled() -> bool:
    """Checklist adjudication (tier c) is OFF, and since 2026-08-16 it is GONE.

    Same batch and same reasoning as _nli_tier_enabled above: hardcoded OFF in
    batch 8b-ii, `_adjudication_pass` deleted by P2-G1, the seam retained for
    the degradation note.
    """
    return False


async def match_checklist(
    items: list,
    test_cases: list[TestCase],
    presented_item_ids: list | None = None,
    allow_llm_tiers: bool = True,
) -> ChecklistCoverage:
    """Bidirectional, EXTERNAL requirement <-> test-case matching (Pass 3).

    FORWARD: every PRESENTED checklist item is either linked to at least one
    case, or reported as a first-class gap (rendered as ``NOT COVERED``) — never
    silently dropped. BACKWARD: every case that links to nothing is reported as
    an orphan (``REVIEW_REQUIRED``) — this generalises qa_ac_anchoring_enforce
    from string-id matching to semantic matching, but it FLAGS ONLY: nothing is
    ever dropped, because TraceLLM reports precision around 0.55 for this class
    of matcher and dropping on a false negative would destroy real coverage.

    ``presented_item_ids``: the ids that actually fitted into the generator's
    prompt (``atomic_checklist.format_checklist_prompt_block`` returns them).
    ``None`` means "all of them". Anything NOT in this set is reported under
    ``not_presented_item_ids`` and is excluded from coverage_pct / gap_rate: the
    generator was never asked to cover it, so counting it as a gap would report
    our own prompt truncation as a requirements failure.

    ``allow_llm_tiers=False`` restricts the matcher to tier (a). The
    checklist-remediation loop uses it so that up to three in-loop matcher calls
    cannot each fire the optional entailment/adjudication calls (that would turn
    "up to 2 extra ask_json calls" into up to 8).

    Never raises — any failure returns a coverage object with ``ran=False`` so
    every caller degrades to today's behaviour."""
    cov = ChecklistCoverage()
    try:
        if not items or not test_cases:
            return cov

        if presented_item_ids is None:
            scored = list(items)
            not_presented: list = []
        else:
            allowed = {str(x) for x in presented_item_ids}
            scored = [it for it in items if it.item_id in allowed]
            not_presented = [it.item_id for it in items if it.item_id not in allowed]
        if not scored:
            # Nothing reached the generator — report that, do NOT report 0%.
            cov.ran = True
            cov.total_items = len(items)
            cov.presented_items = 0
            cov.total_cases = len(test_cases)
            cov.not_presented_item_ids = not_presented
            cov.orphan_tc_ids = [tc.tc_id for tc in test_cases]
            cov.orphan_rate = 100.0
            cov.confidence_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
            cov.notes.append(
                "NONE of the checklist requirements fitted into the generator "
                "prompt (QA_CHECKLIST_MAX_PROMPT_CHARS is too small), so no "
                "coverage percentage can be reported."
            )
            return cov

        item_texts = [_item_text(it) for it in scored]
        case_texts = [_case_match_payload(tc) for tc in test_cases]
        matrix, tier, degraded = await _similarity_matrix(item_texts, case_texts)

        if degraded:
            high = _LEXICAL_HIGH
            medium = _LEXICAL_HIGH
            cov.notes.append(_DEGRADED_NOTE)
        else:
            high = float(getattr(settings, "qa_checklist_match_high", 0.75) or 0.75)
            medium = float(getattr(settings, "qa_checklist_match_medium", 0.62) or 0.62)
            # A misconfigured band must never invert the tiers.
            medium = min(medium, high)

        if not_presented:
            cov.notes.append(
                f"{len(not_presented)} requirement(s) did not fit into the "
                "generator prompt (QA_CHECKLIST_MAX_PROMPT_CHARS) and were never "
                "shown to the model. They are listed under NOT PRESENTED TO "
                "GENERATOR and are EXCLUDED from the coverage percentage and the "
                "gap rate — they are a configuration issue, not a coverage gap. "
                "Raise QA_CHECKLIST_MAX_PROMPT_CHARS (or lower "
                "QA_CHECKLIST_MAX_ITEMS) and re-run. Note that a test written for "
                "one of them can appear here as an orphan."
            )

        links: list[MatchLink] = []
        tiers_on = bool(allow_llm_tiers)
        # Phase 3b: `notes` is this module's OWN established channel for "this
        # measurement was degraded" (see _DEGRADED_NOTE) and is the only one
        # that survives into render_checklist_section, coverage_to_dict, the
        # XLSX checklist sheets and the suite_store payload. Without this the
        # suppression would exist only in the ephemeral chat reply and the
        # EXPORTED artifact would silently look like a full-strength
        # measurement. Only emitted when a tier was genuinely turned off, i.e.
        # when it would otherwise have run.
        #
        # 2026-08-14 (batch 8b-ii): both tier seams are False constants, so
        # this can only fire under a REVIVED seam. KEPT deliberately rather
        # than deleted -- it is the ONLY channel by which the degradation
        # reaches the exported artifact, so without it a one-line revival
        # would silently ship an undisclosed degraded measurement.
        # 2026-08-31 (F7): this note was guarded by the two tier seams, both of
        # which are hardcoded False -- so it could NEVER fire, and the coverage
        # percentage shipped to the workbook with no caveat at all. The
        # limitation it describes is the shipped configuration, not a revived
        # seam, so it is now unconditional.
        # Not on a degraded run: _DEGRADED_NOTE already says the numbers
        # understate and suppresses the percentage outright, and the finalize
        # reply has a 4000-char body cap that a second paragraph saying the
        # same thing pushes past -- measured, the reply truncated at 4386.
        if not tiers_on and not degraded:
            cov.notes.append(
                "Matching is deterministic similarity only: the entailment and "
                "adjudication tiers that re-judged the ambiguous band were "
                "deleted on 2026-08-16. Links under the HIGH band are reported "
                "at MEDIUM confidence rather than discarded (2026-08-31), but "
                "a genuine paraphrase can still fall under both bands, so this "
                "figure UNDERSTATES coverage. Read NOT COVERED as 'no match "
                "found', never as 'not tested'."
            )

        for i, row in enumerate(matrix):
            for j, score in enumerate(row):
                if score < medium:
                    continue
                links.append(
                    MatchLink(
                        item_id=scored[i].item_id,
                        tc_id=test_cases[j].tc_id,
                        score=float(score),
                        confidence=("MEDIUM" if degraded or score < high else "HIGH"),
                        tier=tier,
                    )
                )

        covered = {ln.item_id for ln in links}
        mapped_tcs = {ln.tc_id for ln in links}
        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for ln in links:
            counts[ln.confidence] = counts.get(ln.confidence, 0) + 1

        cov.ran = True
        cov.total_items = len(items)
        cov.presented_items = len(scored)
        cov.total_cases = len(test_cases)
        cov.links = links
        cov.covered_item_ids = [it.item_id for it in scored if it.item_id in covered]
        cov.gap_item_ids = [it.item_id for it in scored if it.item_id not in covered]
        cov.not_presented_item_ids = not_presented
        cov.orphan_tc_ids = [
            tc.tc_id for tc in test_cases if tc.tc_id not in mapped_tcs
        ]
        cov.confidence_counts = counts
        denom = cov.presented_items
        cov.coverage_pct = 100.0 * len(cov.covered_item_ids) / denom if denom else 0.0
        cov.gap_rate = 100.0 * len(cov.gap_item_ids) / denom if denom else 0.0
        cov.orphan_rate = (
            100.0 * len(cov.orphan_tc_ids) / cov.total_cases if cov.total_cases else 0.0
        )
        cov.tier_used = tier
        cov.degraded = degraded
        logger.info(
            "match_checklist: %d/%d presented requirements traced (%s tier), "
            "%d gap(s), %d not presented, %d orphan(s)",
            len(cov.covered_item_ids),
            cov.presented_items,
            tier,
            len(cov.gap_item_ids),
            len(cov.not_presented_item_ids),
            len(cov.orphan_tc_ids),
        )
        return cov
    except Exception:
        logger.exception("match_checklist failed — reporting no coverage data")
        return ChecklistCoverage()


def uncovered_items(coverage: ChecklistCoverage, items: list) -> list:
    """The ChecklistItems still reported as gaps. Never raises.

    Excludes NOT-PRESENTED items by construction: ``gap_item_ids`` only ever
    contains ids that were actually shown to the generator."""
    try:
        if not coverage or not coverage.ran:
            return []
        gaps = set(coverage.gap_item_ids or [])
        return [it for it in items if it.item_id in gaps]
    except Exception:
        logger.exception("uncovered_items failed — returning an empty list")
        return []


def coverage_to_dict(coverage: ChecklistCoverage) -> dict:
    """Plain-dict form for XLSX rows + suite_store persistence. Never raises."""
    try:
        if not coverage or not coverage.ran:
            return {}
        return {
            # Literal True, not coverage.ran: the guard four lines above
            # already returned {} for a falsy ran, so True is the only
            # reachable value and writing it literally keeps the two facts
            # from drifting apart. Absent before 2026-08-19 -- see
            # coverage_from_dict for how the rows written without it default.
            "ran": True,
            "total_items": coverage.total_items,
            "presented_items": coverage.presented_items,
            "total_cases": coverage.total_cases,
            "links": [
                {
                    "item_id": ln.item_id,
                    "tc_id": ln.tc_id,
                    "score": ln.score,
                    "confidence": ln.confidence,
                    "tier": ln.tier,
                }
                for ln in coverage.links
            ],
            "covered_item_ids": list(coverage.covered_item_ids),
            "gap_item_ids": list(coverage.gap_item_ids),
            "not_presented_item_ids": list(coverage.not_presented_item_ids),
            "orphan_tc_ids": list(coverage.orphan_tc_ids),
            "confidence_counts": dict(coverage.confidence_counts),
            # A degraded (lexical) run publishes NO percentage ANYWHERE: not in
            # the tally, not in the XLSX, and not in the persisted payload
            # either — otherwise a later reader of the checklists table could
            # republish the number this report deliberately refuses to print.
            "coverage_pct": None if coverage.degraded else coverage.coverage_pct,
            "gap_rate": None if coverage.degraded else coverage.gap_rate,
            "orphan_rate": None if coverage.degraded else coverage.orphan_rate,
            "tier_used": coverage.tier_used,
            "degraded": coverage.degraded,
            "notes": list(coverage.notes),
        }
    except Exception:
        logger.exception("coverage_to_dict failed — returning an empty dict")
        return {}


def coverage_from_dict(payload: dict) -> ChecklistCoverage:
    """Rehydrate a persisted coverage -- the inverse of ``coverage_to_dict``.

    The symmetric partner of ``atomic_checklist.checklist_from_dicts``, which
    the coverage side went without until 2026-08-19. Every reader therefore
    hand-rolled ``ChecklistCoverage(**payload)``, and that constructor call is
    wrong four ways: it silently loses ``ran`` (so every render/tally guard
    short-circuits to ""), leaves ``links`` as plain dicts rather than
    MatchLinks, puts the deliberate ``None`` a degraded run writes for the
    three rates onto a float field, and raises TypeError on any key a newer
    build added. Never raises.

    SCOPE of the ``ran`` default below: it is exact for a payload written by
    ``coverage_to_dict``, which is what the ``checklists`` table holds. A dict
    hand-built by a caller can of course omit ``ran`` without having run
    anything -- such a caller should pass ``ran`` explicitly."""
    cov = ChecklistCoverage()
    try:
        if not payload:
            return cov
        # ``ran`` was not persisted before 2026-08-19. It did not need to be:
        # coverage_to_dict returns {} unless coverage.ran, so a NON-EMPTY
        # payload is itself proof that the matcher ran. That is what defaults
        # the pre-existing rows -- no migration, and it is total rather than a
        # heuristic. Deriving it from ``tier_used`` instead would be WRONG: the
        # "nothing fitted into the prompt budget" branch above runs with
        # tier_used "", and that branch is exactly the diagnostic a re-audit
        # most wants to recover.
        cov.ran = bool(payload.get("ran", True))
        cov.total_items = int(payload.get("total_items") or 0)
        cov.presented_items = int(payload.get("presented_items") or 0)
        cov.total_cases = int(payload.get("total_cases") or 0)
        for row in payload.get("links") or []:
            try:
                cov.links.append(
                    MatchLink(
                        item_id=str(row.get("item_id") or ""),
                        tc_id=str(row.get("tc_id") or ""),
                        score=float(row.get("score") or 0.0),
                        confidence=str(row.get("confidence") or ""),
                        tier=str(row.get("tier") or ""),
                    )
                )
            except Exception:
                logger.debug("skipping a malformed coverage link", exc_info=True)
        cov.covered_item_ids = list(payload.get("covered_item_ids") or [])
        cov.gap_item_ids = list(payload.get("gap_item_ids") or [])
        cov.not_presented_item_ids = list(payload.get("not_presented_item_ids") or [])
        cov.orphan_tc_ids = list(payload.get("orphan_tc_ids") or [])
        cov.confidence_counts = dict(payload.get("confidence_counts") or {})
        # None on the wire is DELIBERATE -- a degraded run publishes no
        # percentage anywhere, including in the persisted payload. 0.0 is the
        # dataclass default and is never read on that path, because
        # ``degraded`` gates every branch that formats one. Restoring None
        # instead would put a None on a float field and turn the first
        # f-string that touched it into an exception.
        cov.coverage_pct = float(payload.get("coverage_pct") or 0.0)
        cov.gap_rate = float(payload.get("gap_rate") or 0.0)
        cov.orphan_rate = float(payload.get("orphan_rate") or 0.0)
        cov.tier_used = str(payload.get("tier_used") or "")
        cov.degraded = bool(payload.get("degraded"))
        cov.notes = list(payload.get("notes") or [])
    except Exception:
        logger.exception("coverage_from_dict failed -- reporting no coverage data")
        return ChecklistCoverage()
    return cov


def checklist_tally_line(coverage: ChecklistCoverage) -> str:
    """The one-line bidirectional tally. "" when the matcher didn't run.

    DEGRADED (lexical) RUNS PUBLISH NO PERCENTAGE. TF-IDF cosine between an EARS
    requirement and a test payload rarely clears _LEXICAL_HIGH for a genuine
    paraphrase, so a correctly-covered suite would read as "3/44 (7%)". A bold
    number with a caveat underneath is still read as a number, so the number is
    removed and the line is stamped UNRELIABLE instead."""
    try:
        if not coverage or not coverage.ran or not coverage.total_items:
            return ""
        counts = coverage.confidence_counts or {}
        mapped = coverage.total_cases - len(coverage.orphan_tc_ids)
        tests = (
            f"Tests: {coverage.total_cases} ({mapped} mapped, "
            f"{len(coverage.orphan_tc_ids)} orphan(s)); "
        )
        confidence = (
            f"Confidence: {counts.get('HIGH', 0)} HIGH, "
            f"{counts.get('MEDIUM', 0)} MEDIUM, {counts.get('LOW', 0)} LOW; "
            "Mutation effectiveness: UNKNOWN."
        )
        not_presented = len(coverage.not_presented_item_ids or [])
        if not coverage.presented_items:
            # Nothing was shown to the generator, so there is no coverage to
            # measure. "0/0 (0%)" would read as a catastrophic result when the
            # real problem is a configuration one.
            return (
                "Coverage: NOT MEASURED — none of the "
                f"{coverage.total_items} requirement(s) fitted into the generator "
                "prompt, so nothing could be scored (raise "
                f"QA_CHECKLIST_MAX_PROMPT_CHARS). {tests}{confidence}"
            )
        suffix = ""
        if not_presented:
            suffix = (
                f" [{not_presented} of {coverage.total_items} requirement(s) were "
                "NOT PRESENTED to the generator and are excluded from this "
                "figure.]"
            )
        if coverage.degraded:
            return (
                "UNRELIABLE (lexical fallback — no embeddings backend): coverage "
                "percentage SUPPRESSED. "
                f"{len(coverage.covered_item_ids)} of {coverage.presented_items} "
                "requirement(s) matched lexically, which UNDERSTATES real "
                f"coverage and is not a coverage figure; {tests}{confidence}"
                f"{suffix}"
            )
        return (
            f"Coverage: {len(coverage.covered_item_ids)}/{coverage.presented_items} "
            f"requirements traced ({coverage.coverage_pct:.0f}%, "
            f"{len(coverage.gap_item_ids)} gap(s)); "
            f"{tests}{confidence}{suffix}"
        )
    except Exception:
        logger.exception("checklist_tally_line failed — returning empty string")
        return ""


def checklist_oneline(coverage: ChecklistCoverage) -> str:
    """Compact summary line for the deferred (MCP) path. Never raises."""
    try:
        line = checklist_tally_line(coverage)
        return f"\n\n**Requirements checklist:** {line}" if line else ""
    except Exception:
        logger.exception("checklist_oneline failed — returning empty string")
        return ""


# Lexical-tier stand-in for FOUR fixed prose blocks: the "SECOND, independent
# coverage view" paragraph, _DEGRADED_NOTE, PROVENANCE_LIMITATION and the closing
# HONESTY_BOUNDARY. Those four are ~1.5-2.0 KB repeated verbatim on every run,
# wrapping a tally line that already says UNRELIABLE / SUPPRESSED -- and on a real
# run (96 cases, 18 items, 2026-08-16) they pushed the finalize reply to 4546
# chars against the 4000-char cap in
# tools/mcp_handlers.shape_generation_result, so the Test Data list was cut off
# mid-enumeration. Every CLAIM they make survives here (additive-not-RTM,
# lexical understates, self-reported provenance, textual-alignment-only, and
# where the full detail lives); only the length is spent differently. The
# EMBEDDINGS tier still renders the full prose, byte-identically to before.
#
# It deliberately does NOT restate the tier verdict: the BOLD TALLY LINE
# directly above it already reads "UNRELIABLE (lexical fallback — no embeddings
# backend): coverage percentage SUPPRESSED ... which UNDERSTATES real coverage".
# What survives here is the four claims that appear nowhere else in the section.
_LEXICAL_COMPACT_CAVEAT = (
    "_Set `QA_EMBEDDINGS_BACKEND` for a usable audit. "
    "Provenance tags are SELF-REPORTED. Textual alignment only — NOT a "
    "verification-strength guarantee. Full detail (per-requirement and orphan "
    "lists) is on the 'Requirements Checklist' workbook sheet._"
)


# The reconciliation between THIS figure and the RTM's used to live in the
# caveat above -- i.e. at the END of the section, roughly 900 characters after
# the tester has already read "**Requirements:** 7/7 acceptance criteria traced,
# all covered" and then met "UNRELIABLE ... 13 of 20". A non-technical tester
# read the pair as contradictory (F10), which is a fair reading: nothing at the
# point of first contact said the second number measures something else.
#
# So it is rendered BEFORE the bold tally instead, and the clause was DELETED
# from the caveat rather than copied -- the claim moves, it is not repeated, and
# the finalize reply has 261 characters of headroom against
# tools/mcp_handlers._SUMMARY_CAP on the 96-case fixture (net cost here: +114).
#
# LEXICAL TIER ONLY. The embeddings branch already carries the full "SECOND,
# independent coverage view" paragraph and its output is pinned byte-identical
# by tests/test_checklist_matcher.test_embeddings_section_prose_is_byte_identical.
_LEXICAL_LEAD = (
    "_A SECOND, different measurement — not the **Requirements** line above. "
    "It counts different things a different way, so the two are NOT expected "
    "to agree, and a low number here contradicts nothing._"
)


def render_checklist_section(coverage: ChecklistCoverage, items: list) -> str:
    """Full markdown coverage report: tally, NOT PRESENTED items, NOT COVERED
    gaps, REVIEW_REQUIRED orphans, provenance caveats (comment-derived
    requirements are called out HERE, not only in the spreadsheet), and the
    mandatory honesty boundary. "" when the matcher did not run. Never raises."""
    try:
        # A coverage that genuinely did NOT run is a BARE ChecklistCoverage():
        # both no-run returns in match_checklist leave every field at its
        # dataclass default. So an object that denies running while CARRYING
        # run data did not come from the matcher -- it came from
        # ``ChecklistCoverage(**persisted_payload)``, which drops ``ran``. That
        # produced a silent "" for six stored suites and no log line at all
        # (F12), because the legitimate no-run path returns "" too. Warn on the
        # malformed shape ONLY; the legitimate one stays silent, byte-identical
        # to before. Inside the try, and a logger call on dataclass attribute
        # reads cannot throw, so "never raises" is unaffected.
        if (
            coverage
            and not coverage.ran
            and (
                coverage.tier_used
                or coverage.total_cases
                or coverage.total_items
                or coverage.links
            )
        ):
            logger.warning(
                "render_checklist_section: coverage.ran is False but the "
                "object carries run data (tier=%r, %d case(s), %d "
                'requirement(s)) -- returning "". This is the signature of '
                "a persisted coverage rebuilt with ChecklistCoverage(**payload); "
                "use rtm.coverage_from_dict() instead.",
                coverage.tier_used,
                coverage.total_cases,
                coverage.total_items,
            )
        if not coverage or not coverage.ran or not items:
            return ""
        by_id = {it.item_id: it for it in items}

        def _label(item_id: str) -> str:
            it = by_id.get(item_id)
            if not it:
                return ""
            source = getattr(it, "source", "") or "unattributed"
            return f"{_item_text(it)} _[source: {source}]_"

        # On the lexical fallback the four fixed prose blocks collapse into
        # _LEXICAL_COMPACT_CAVEAT (appended last, where HONESTY_BOUNDARY would
        # otherwise sit). `compact` is False on the embeddings tier, so that
        # tier's output is byte-identical to before.
        compact = bool(coverage.degraded)
        lines = ["\n\n---\n\n## Requirements Checklist Coverage (bidirectional)"]
        if compact:
            # BEFORE the tally, deliberately: the number is what the reader
            # reacts to, so the framing has to arrive first. See _LEXICAL_LEAD.
            lines += ["", _LEXICAL_LEAD]
        lines += [
            "",
            f"**{checklist_tally_line(coverage)}**",
            "",
            f"_Matcher tier: {coverage.tier_used}._",
        ]
        if not compact:
            lines += [
                "",
                "_This is a SECOND, independent coverage view; it does not replace "
                "the Requirements Traceability Matrix above, and the two figures are "
                "not expected to agree. The RTM counts ACCEPTANCE CRITERIA that the "
                "test cases tagged themselves against (self-reported, one row per "
                "AC). This section counts ATOMIC REQUIREMENTS matched EXTERNALLY "
                "from each case's expected results, ignoring those tags — a "
                "different denominator, computed a different way._",
            ]
        for note in coverage.notes or []:
            # _DEGRADED_NOTE only ever appears WITH coverage.degraded, and the
            # compact caveat already carries its claim. Every OTHER note (e.g.
            # the host NLI-tier suppression note) still prints in full.
            if compact and note == _DEGRADED_NOTE:
                continue
            lines += ["", f"> {note}"]
        for caveat in provenance_caveats(items):
            # Same treatment for the unconditional self-reported-provenance
            # blockquote. The comment-derived and unattributed WARNINGS are
            # per-run findings, not boilerplate, and are never dropped.
            if compact and caveat == PROVENANCE_LIMITATION:
                continue
            lines += ["", f"> {caveat}"]

        not_presented = coverage.not_presented_item_ids or []
        if not_presented:
            lines += [
                "",
                "### NOT PRESENTED TO GENERATOR (excluded from the coverage figure)",
                "",
                "These requirements did not fit inside the prompt budget, so the "
                "generator never saw them. They are NOT counted as gaps — this is "
                "a configuration issue, not a coverage result:",
                "",
            ]
            for nid in not_presented[:50]:
                lines.append(f"- **NOT PRESENTED: {nid}** — {_label(nid)}")
            if len(not_presented) > 50:
                lines.append(f"- … and {len(not_presented) - 50} more")

        gaps = coverage.gap_item_ids or []
        orphans = coverage.orphan_tc_ids or []
        if compact:
            # On the lexical tier BOTH directions are dominated by matcher
            # misses rather than findings: TF-IDF cosine rarely clears the
            # threshold for a genuine paraphrase, which is why the tally above
            # already SUPPRESSES the percentage. One line per unmatched
            # requirement plus 30 orphan ids is a flood of near-certainly-false
            # review work AND, on a real run (18 items, 96 cases, 2026-08-16),
            # it pushed the reply past the 4000-char cap in
            # tools/mcp_handlers.shape_generation_result so genuine content was
            # truncated. ONE block with one counted bullet per direction: two
            # headings and two repetitions of "see the exported workbook" were
            # themselves a measurable part of the overflow. Both full lists
            # still ship on the 'Requirements Checklist' sheet.
            unmatched = []
            if gaps:
                unmatched.append(
                    f"- {len(gaps)} of {coverage.presented_items} requirement(s) "
                    "had no lexical match. This is **NOT a gap list** — most are "
                    "near-certainly covered."
                )
            if orphans:
                unmatched.append(
                    f"- {len(orphans)} case(s) matched no checklist requirement "
                    "(REVIEW_REQUIRED orphans) — a matcher weakness on this "
                    "tier, not a finding."
                )
            if unmatched:
                lines += ["", "### Unmatched (lexical tier)", ""] + unmatched
        else:
            if gaps:
                lines += ["", "### NOT COVERED (forward gaps)", ""]
                for gid in gaps[:50]:
                    lines.append(
                        f"- **NOT COVERED: {gid}** — {_label(gid)} "
                        "(no test case matched this requirement above the "
                        "configured threshold)"
                    )
                if len(gaps) > 50:
                    lines.append(f"- … and {len(gaps) - 50} more")

            if orphans:
                lines += ["", "### REVIEW_REQUIRED (backward orphans)", ""]
                lines.append(
                    "These test cases matched no checklist requirement. That is "
                    "EITHER undocumented behaviour being tested OR a sign the "
                    "checklist is under-decomposed — the matcher cannot tell "
                    "which. Nothing was dropped:"
                )
                lines.append(
                    "- " + ", ".join(orphans[:30]) + (" …" if len(orphans) > 30 else "")
                )

        low = (coverage.confidence_counts or {}).get("LOW", 0)
        if low:
            lines += [
                "",
                f"> {low} match(es) were resolved by LLM adjudication only "
                "(LOW confidence) and require human review.",
            ]

        lines += ["", _LEXICAL_COMPACT_CAVEAT if compact else HONESTY_BOUNDARY]
        return "\n".join(lines)
    except Exception:
        logger.exception("render_checklist_section failed — returning empty string")
        return ""
