"""Case-intake stage of qa_submit_suite (moved from handlers, C3a step 2a).

Snapshot of what the HOST actually sent, taken before anything narrows
``all_cases``: the duplicate and coverage reviews resolve the ids they were
given against it, so it keeps meaning "the submission" even after the
grounding review re-files a case out of the executable suite.

``staged_rows`` is read ONCE here and PASSED to every refusal and hand-back
later (none re-reads the store), so they all describe the same reality. It is
ZERO on the merged route: ``has_full`` means the staged rows were explicitly
NOT used.

F6: on the FULL-suite path the per-case ``category`` is host self-report, so
it is normalised onto a canonical name, blanked when unresolvable, and the
provenance is TAGGED so a re-export can tell it from a server-derived value.
"""

from __future__ import annotations

import logging

from agents.host_category_jobs import normalize_category
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubCases, SubRead
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


def _canonicalise(all_cases: list) -> int:
    resolved = 0
    for tc in all_cases:
        canon = normalize_category(getattr(tc, "category", None))
        # pragma: no cover - defensive
        with never_raise(logger, "could not set category", exc_info=True):
            tc.category = canon or None
            tc.category_source = "host" if canon else None
        if canon:
            resolved += 1
    return resolved


def self_reported_category_note(all_cases: list) -> str:
    resolved = _canonicalise(all_cases)
    unresolved = len(all_cases) - resolved
    cat_source = (
        f"> ℹ️  Category resolved for {resolved} case(s)"
        + (f", unresolved for {unresolved}" if unresolved else "")
        + " -- **self-reported by your chat model**, because a single "
        "merged submission carries no server-side grouping. Submit per "
        "category with `qa_submit_category` for a server-derived "
        "category instead.\n\n"
    )
    return cat_source


async def case_intake(prep_id: str, read: SubRead, ports: SubmitPorts) -> SubCases:
    all_cases = list(read.parsed.suite.test_cases)
    staged_rows = 0 if read.has_full else await ports.category.staged_row_count(prep_id)
    submitted_cases = list(all_cases)
    cat_source = ""
    if read.has_full:
        cat_source = self_reported_category_note(all_cases)
    return SubCases(all_cases, staged_rows, submitted_cases, cat_source)
