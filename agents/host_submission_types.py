"""Host mode: the ParsedSubmission result of parse_host_suite."""

from __future__ import annotations

import dataclasses

from tools.models import TestSuite


@dataclasses.dataclass
class ParsedSubmission:
    """Result of parse_host_suite. Carries the validated suite AND the salvage
    delta so ops-3d can ALWAYS tell the tester "N case(s) were dropped as
    malformed" -- independent of checklist config. Silence about
    dropped cases was the thing being fixed: without this, a host submitting 40
    cases of which 39 are malformed would yield a silent 1-case suite presented
    as finished."""

    suite: TestSuite
    dropped_count: int = 0
    dropped_reasons: list = dataclasses.field(default_factory=list)
    # 2026-08-31 (F9): cases KEPT after their OPTIONAL `test_data` plan failed
    # validation. Deliberately distinct from dropped_reasons -- the case is in
    # the suite and the plan is not, and saying exactly that is the difference
    # between a disclosure and a silent partial accept.
    salvaged_reasons: list = dataclasses.field(default_factory=list)
    # Piece 1: the host's OPTIONAL cross-category duplicate review, SHAPE-validated
    # against this suite's tc_ids (see _extract_duplicate_groups). Still unscreened
    # -- screen_duplicate_groups applies the safety bounds before anything is
    # removed. Empty on the per-category path, where the field cannot be used.
    duplicate_groups: list = dataclasses.field(default_factory=list)
    # True when the submission actually CARRIED a `duplicate_groups` key, however
    # malformed. Distinguishes "the host reviewed and found no duplicates" from
    # "the host ignored the request" -- both yield an empty list, but only the
    # second means no review happened. Always False on the per-category merge
    # path, where merge_category_rows structurally drops the field.
    duplicate_review_offered: bool = False
    # Why part of that field was rejected. Surfaced in the reply -- silence about a
    # rejected untrusted field is the failure mode being avoided.
    duplicate_notes: list = dataclasses.field(default_factory=list)
    # The host's OPTIONAL `requirement_matches` field, carried RAW and
    # UNVALIDATED. It is popped here (before TestSuite validation, which sets
    # extra="forbid") so a stray field from a host still following an older
    # prompt cannot fail an otherwise valid submit. NOTHING READS IT since the
    # host coverage review was deleted (2026-08-12); it is kept for that
    # tolerance alone.
    raw_requirement_matches: object = None
    # The host's OPTIONAL `acceptance_criteria` field (the AC boomerang job's
    # return_field), carried RAW and UNVALIDATED for exactly the same reason:
    # it is popped before TestSuite validation (extra="forbid") but validated
    # later, in extract_host_acs. Nothing may read it without validating it.
    raw_acceptance_criteria: object = None
    # The entailment review's OPTIONAL `grounding_verdicts`, raw and unvalidated,
    # for the same reason: popped before TestSuite validation (extra="forbid") and
    # validated later, in tools.grounding_verdicts.parse_verdicts, which matches
    # every id against the suite that was actually submitted. Absent is NOT a
    # failure -- the review is optional, so an absent field just means no
    # grounding report.
    raw_grounding_verdicts: object = None
    # The ambiguity job's OPTIONAL `ambiguity_result`, raw and unvalidated.
    # Absent is meaningful here: it means the blocking safety preflight
    # left no evidence it ran (see extract_ambiguity_result).
    raw_ambiguity_result: object = None
    # The image job's OPTIONAL `image_descriptions`, raw and unvalidated.
    # Absent is NOT a failure here: the job is non-blocking, so an absent field
    # only means the server has no record of what the screenshots showed.
    raw_image_descriptions: object = None
    # Residue R4: the checklist job's OPTIONAL `checklist_items` field, raw and
    # unvalidated. Popped before TestSuite validation (extra="forbid") and
    # validated later in extract_host_checklist. Absent means NO checklist: the
    # server does not decompose the ticket to fill the gap.
    raw_checklist_items: object = None
    # K-09/K-13: the host's per-pair duplicate justifications and its
    # confirmation of the quality warnings, raw; read only by host_submit_suite_gate.
    raw_duplicate_justifications: object = None
    raw_confirm_warnings: object = None
