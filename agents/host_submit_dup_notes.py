"""Duplicate-review notes of qa_submit_suite (moved from handlers, C3a step 2d).

Status note: set on BOTH finalize paths with DIFFERENT wording (F11). The full
suite path can fairly say the host did not send the field; the merge path must
blame the route, because the SERVER discards `duplicate_groups` there. An
HONEST EMPTY SIDECAR (a sidecar whose `duplicate_groups` is []) sets
duplicate_review_offered, so it is told apart from "no review ran" (Fix 2 / H4).
D4 (2026-08-21): the empty-result wording is attributed to the HOST, never a
flat "ran and reported none", because the server prescreen recovers only about
one redundant cluster in nine; a false assurance standing unchallenged is the
whole of D4. The text replaces, not adds to, a protected section.

Prescreen: the host's optional cross-category duplicate review rode in on the
submission; when it claims none the server title prescreen is shown beside it.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from agents.host_dedup_lines import DupSuites
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubDup
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")

_HOST_NONE = (
    "> ♻️  Duplicate review ran: the HOST "
    "reported no cross-category duplicates -- its verdict, "
    "not a server check. The server's title prescreen is a "
    "weak floor (1 of 9 clusters, measured), so neither "
    "empty result is evidence.\n\n"
)
_NO_SIDECAR = (
    "> ℹ️  No duplicate review ran: this suite was "
    "finalized from per-category rows with no `duplicate_groups` "
    "sidecar. Either submit ONE merged `suite_json`, or finalize with "
    '`suite_json={"duplicate_groups":[[...]]}` (empty/absent '
    "`test_cases`) after staging categories -- send an EMPTY "
    "`duplicate_groups` list (`[]`) there if you DID review and "
    "found none, which records the review as run. Any "
    "cross-category duplicates are still present.\n\n"
)
_NO_FIELD = (
    "> ℹ️  Duplicate review was requested but this "
    "submission carried no `duplicate_groups` field, so NO "
    "duplicate review ran -- which is NOT the same as finding "
    "none. To report a review that found none, send the field "
    'as an EMPTY list (`"duplicate_groups": []`). Any '
    "cross-category duplicates are still present.\n\n"
)


class StatusIn(NamedTuple):
    parsed: Any
    has_full: bool
    groups: list
    review_on: bool


class SectionIn(NamedTuple):
    dup: SubDup
    submitted_cases: list
    suite: Any


class PrescreenIn(NamedTuple):
    suite: Any
    parsed: Any
    dup: SubDup


def status_note(args: StatusIn) -> str:
    if not args.review_on or args.groups:
        return ""
    if getattr(args.parsed, "duplicate_review_offered", False):
        return _HOST_NONE
    return _NO_FIELD if args.has_full else _NO_SIDECAR


def section_note(args: SectionIn, ports: SubmitPorts) -> str:
    dup = args.dup
    dup_note = dup.note
    if dup.review_on:
        dup_note = ports.dup.build_duplicate_section(
            dup.groups,
            DupSuites(
                args.submitted_cases,
                list(getattr(args.suite, "test_cases", None) or []),
            ),
            removed=dup.removed,
            applied=dup.apply,
            notes=dup.notes,
            agreements=dup.agreements,
        )
    return dup_note


def prescreen_head(suite: Any, ports: SubmitPorts) -> tuple[str, Any]:
    cases_json = ports.media.dup_shortlist_cases_json(
        list(getattr(suite, "test_cases", None) or [])
    )
    pairs, total = ports.media.build_dup_shortlist_counted(cases_json)
    return ports.media.build_dup_contradiction_headline(len(pairs), total), pairs


def prescreen_notes(args: PrescreenIn, ports: SubmitPorts) -> tuple[str, str]:
    head = ""
    pairs = ""
    claimed_none = bool(getattr(args.parsed, "duplicate_review_offered", False))
    with never_raise(logger, "merged-path duplicate prescreen failed", exc_info=True):
        if args.dup.review_on and not args.dup.groups and claimed_none:
            # head is set before the pairs build, so a raise there keeps it
            head, raw = prescreen_head(args.suite, ports)
            if head:
                pairs = ports.media.build_dup_contradiction_pairs(raw)
    return head, pairs
