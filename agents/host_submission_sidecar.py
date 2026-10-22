"""Host mode: the submission-level sidecar fields popped off a candidate suite."""

from __future__ import annotations

from typing import NamedTuple

from agents.host_dedup import _extract_duplicate_groups
from agents.host_submission_types import ParsedSubmission
from tools.models import TestSuite


class _Sidecar(NamedTuple):
    raw_groups: object
    fields: dict


# Pop every submission-level key off a COPY of ``data``.
#
# Returns ``(copy, raw_duplicate_groups, sidecar)``; ``sidecar`` holds the
# ``ParsedSubmission`` kwargs that both of ``_validate_suite``'s returns share.
# Piece 1: duplicate_groups is a SUBMISSION-level field, not a TestSuite field
# (TestSuite is also the LLM response_model, so its schema must stay clean), and
# TestSuite sets extra="forbid" -- so pop it from a COPY before validating.
# Popping also keeps the fast path working: without it, a submission carrying the
# field would ALWAYS fail whole-suite validation and fall into the salvage branch.
# Piece 2: same reasoning, one field further -- pop it from the COPY so a
# submission carrying it still takes the fast whole-suite validation path.
# Same reasoning again for the AC boomerang's return field.
# Same again for the entailment review's verdicts.
# ...and the ambiguity job's verdict, which is what makes its
# `blocking: True` observable to the server at all.
# ...and the image job's descriptions of the screenshots this server
# forwarded to the host INSTEAD of describing them itself.
# Residue R4: the checklist job's return field. Leaving it in place would
# push EVERY submission carrying it into the salvage branch and silently
# drop cases. It reaches BOTH return sites through this one dict, so the
# checklist cannot vanish on the weak-host submissions that need it most.
# The two Phase-3a post_merge return fields. RISK_JOB and TEST_PLAN_JOB were
# DELETED on 2026-08-16 (dead-code deletion P2-H) and nothing reads either
# field now -- but the POPS stay, because they never depended on the jobs.
# TestSuite sets extra="forbid", so a submission carrying a stray
# `risk_scores` or `test_plan_report` key would fail whole-suite validation
# and drop into the salvage branch, silently losing cases. The values are
# discarded.
def _pop_sidecar_fields(data) -> tuple[dict, _Sidecar]:
    data = dict(data) if isinstance(data, dict) else {}
    sidecar = {
        "duplicate_review_offered": "duplicate_groups" in data,
        "raw_requirement_matches": data.pop("requirement_matches", None),
        "raw_acceptance_criteria": data.pop("acceptance_criteria", None),
        "raw_grounding_verdicts": data.pop("grounding_verdicts", None),
        "raw_ambiguity_result": data.pop("ambiguity_result", None),
        "raw_image_descriptions": data.pop("image_descriptions", None),
        "raw_checklist_items": data.pop("checklist_items", None),
        "raw_duplicate_justifications": data.pop("duplicate_justifications", None),
        "raw_confirm_warnings": data.pop("confirm_quality_warnings", None),
    }
    data.pop("risk_scores", None)
    data.pop("test_plan_report", None)
    return data, _Sidecar(data.pop("duplicate_groups", None), sidecar)


def _parsed_submission(
    suite: TestSuite, side: _Sidecar, drops: dict | None = None
) -> ParsedSubmission:
    """The one ``ParsedSubmission`` build both ``_validate_suite`` paths return.

    ``drops`` carries the salvage path's dropped/salvaged delta; the fast path has none.
    """
    groups, dup_notes = _extract_duplicate_groups(
        side.raw_groups, {tc.tc_id for tc in suite.test_cases}
    )
    return ParsedSubmission(
        suite=suite,
        duplicate_groups=groups,
        duplicate_notes=dup_notes,
        **side.fields,
        **(drops or {}),
    )
