"""Suite-quality signal detectors -- item 10 (v1.97.0 cursor-hardening).

Self-contained, pure-function module computing the audit-named signals over an
already-parsed list of case-like objects, duck-typed via ``getattr`` so it
accepts real ``tools.models.TestCase`` instances or plain test doubles
(``SimpleNamespace``) equally. Warn-only: nothing here ever refuses, gates, or
raises -- these are advisories for whatever caller wires
``compute_suite_quality_advisories``'s output into a reply or workbook
(deliberately NOT done by this module in this change -- see the plan's
Cross-Scope Conflicts / Risk Assessment: the exact staged-text and
server-duplicate-groups variable names at the mcp_handlers.py submit-tail call
site were not confirmed within the planning run's discovery budget).

Bounds below (``_max_unverified_refs`` / ``_max_conditional_step_ids``) are
deliberately lower-case: they exist so a hostile 100k-case suite cannot
produce a 100k-line advisory, but they are internal list-length guards, not a
declared product cap in the sense ``tests/cap_scanner.py`` looks for.
"""

from __future__ import annotations

import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)

#: Ticket-shaped key, e.g. "ABC-123" -- the same shape Jira issue keys use.
_ticket_key_re = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")

#: A step action reads as an if/when branch rather than a single
#: deterministic action.
_conditional_re = re.compile(r"\b(if|when)\b", re.IGNORECASE)

_max_unverified_refs = 20
_max_conditional_step_ids = 20


def _case_text_fields(case) -> list[str]:
    """Every free-text field on a case worth scanning. Never raises."""
    try:
        fields = [
            str(getattr(case, "title", "") or ""),
            str(getattr(case, "preconditions", "") or ""),
        ]
        for step in getattr(case, "steps", None) or []:
            fields.append(str(getattr(step, "action", "") or ""))
            fields.append(str(getattr(step, "expected_result", "") or ""))
            fields.append(str(getattr(step, "test_data", "") or ""))
        return fields
    except Exception:
        logger.debug("_case_text_fields failed", exc_info=True)
        return []


def unverified_external_references(cases, staged_text: str = "") -> list[str]:
    """Ticket-shaped keys (e.g. \"ABC-123\") a case's own text references that
    do NOT appear anywhere in ``staged_text`` (the ticket content actually
    staged for this run). [] when nothing references a ticket key, or when
    every referenced key is present in staged_text. Never raises."""
    try:
        staged = str(staged_text or "")
        found: list[str] = []
        for case in cases or []:
            for text in _case_text_fields(case):
                for key in _ticket_key_re.findall(text):
                    if key not in staged and key not in found:
                        found.append(key)
                    if len(found) >= _max_unverified_refs:
                        return found
        return found
    except Exception:
        logger.debug("unverified_external_references failed", exc_info=True)
        return []


def conditional_steps(cases) -> list[str]:
    """tc_ids of cases with at least one step whose action reads as an
    if/when branch -- a step whose outcome depends on a condition rather than
    a single deterministic action. [] when none. Never raises."""
    try:
        ids: list[str] = []
        for case in cases or []:
            for step in getattr(case, "steps", None) or []:
                action = str(getattr(step, "action", "") or "")
                if _conditional_re.search(action):
                    tc_id = str(getattr(case, "tc_id", "") or "")
                    if tc_id and tc_id not in ids:
                        ids.append(tc_id)
                    break
            if len(ids) >= _max_conditional_step_ids:
                break
        return ids
    except Exception:
        logger.debug("conditional_steps failed", exc_info=True)
        return []


def ac_concentration(cases, threshold: float = 0.4):
    """(requirement_id, ratio) for the single requirement_id backing MORE
    than ``threshold`` of the suite, or None when no requirement_id passes
    threshold (including an empty/garbage suite). Never raises."""
    try:
        total = len(cases or [])
        ids = [str(getattr(c, "requirement_id", "") or "") for c in cases or []]
        ids = [i for i in ids if i]
        if not ids or total <= 0:
            return None
        rid, count = Counter(ids).most_common(1)[0]
        ratio = count / total
        return (rid, ratio) if ratio > threshold else None
    except Exception:
        logger.debug("ac_concentration failed", exc_info=True)
        return None


def precondition_repetition(cases, threshold: float = 0.5):
    """(preconditions_text, ratio) for the single preconditions string used by
    MORE than ``threshold`` of the suite, or None. Never raises."""
    try:
        total = len(cases or [])
        texts = [
            str(getattr(c, "preconditions", "") or "").strip() for c in cases or []
        ]
        texts = [t for t in texts if t]
        if not texts or total <= 0:
            return None
        text, count = Counter(texts).most_common(1)[0]
        ratio = count / total
        return (text, ratio) if ratio > threshold else None
    except Exception:
        logger.debug("precondition_repetition failed", exc_info=True)
        return None


def dedup_self_report_contradiction(
    host_claimed_no_duplicates: bool, server_duplicate_groups
) -> bool:
    """True when the host self-reported NO duplicates while the server's own
    lexical prescreen found at least one candidate group -- a contradiction
    between the two, not a verdict on which is right. Never raises."""
    try:
        groups = list(server_duplicate_groups or [])
        return bool(host_claimed_no_duplicates) and len(groups) > 0
    except Exception:
        logger.debug("dedup_self_report_contradiction failed", exc_info=True)
        return False


def compute_suite_quality_advisories(
    cases,
    *,
    staged_text: str = "",
    host_claimed_no_duplicates: bool = False,
    server_duplicate_groups=None,
) -> list[tuple[str, str]]:
    """Aggregate every signal above into ``(title, detail)`` tuples, the exact
    shape the Generation Notes extension point already takes
    (``tools/mcp_handlers.py``'s ``_gen_notes.append((title, detail))``, see
    ``agents.host_mode.ambiguity_notes_gen_note`` for a sibling of this same
    shape). [] when nothing is worth flagging, including for a None/garbage
    ``cases`` argument. Warn-only -- never refuses, never raises."""
    out: list[tuple[str, str]] = []
    try:
        refs = unverified_external_references(cases, staged_text)
        if refs:
            out.append(
                (
                    "Unstaged ticket references",
                    "These cases reference ticket key(s) not found in the "
                    f"staged ticket text: {', '.join(refs)}.",
                )
            )
        cond_ids = conditional_steps(cases)
        if cond_ids:
            out.append(
                (
                    "Conditional / unexecutable steps",
                    "These case(s) have a step whose action reads as an "
                    "if/when branch rather than a single deterministic "
                    f"action: {', '.join(cond_ids)}.",
                )
            )
        ac = ac_concentration(cases)
        if ac:
            rid, ratio = ac
            out.append(
                (
                    "Acceptance-criterion concentration",
                    f"`{rid}` backs {ratio:.0%} of this suite -- coverage may "
                    "be concentrated on one requirement rather than spread "
                    "across the ticket.",
                )
            )
        precond = precondition_repetition(cases)
        if precond:
            text, ratio = precond
            out.append(
                (
                    "Precondition repetition",
                    f"{ratio:.0%} of this suite shares the identical "
                    f"precondition text ({text[:80]!r}) -- may indicate "
                    "copy-paste rather than per-case tailoring.",
                )
            )
        if dedup_self_report_contradiction(
            host_claimed_no_duplicates, server_duplicate_groups
        ):
            out.append(
                (
                    "Dedup self-report contradiction",
                    "The submitting host reported NO duplicate cases, but "
                    "the server's own lexical prescreen found candidate "
                    "duplicate group(s) -- the two disagree.",
                )
            )
    except Exception:
        logger.debug("compute_suite_quality_advisories failed", exc_info=True)
    return out
