"""The prepared-generation record and the host AC-job directive."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tools.atomic_checklist import ChecklistItem
from tools.rtm import AcceptanceCriterion

if TYPE_CHECKING:
    from tools.rule_packs import RulePackResult


# Injected into the SHARED category system prompt (via rtm_hint) when, and only
# when, _prepare_generation was asked NOT to synthesize acceptance criteria and
# the ticket carried none -- i.e. host mode with QA_HOST_AC_REVIEW_ENABLED.
#
# WORKER-FACING, and that is the whole point of its wording. This block travels
# inside ``system_prompt``, which under QA_HOST_PARALLEL_FANOUT_ENABLED is handed
# VERBATIM to all 8 per-category workers by build_category_job. An earlier draft
# said "derive 3-8 acceptance criteria" here; read by a worker, that instructs
# EACH of the 8 to derive its OWN AC-001..AC-00N list, and the merged suite then
# carries colliding ids meaning different things per category -- which
# extract_host_acs' id reassignment would silently re-point again. So derivation
# lives ONLY in the parent-facing job spec (agents.host_ac.AC_JOB); this half
# says the list is SUPPLIED, forbids deriving or renumbering, and forbids
# inventing an id when no list arrived (null is correct there, a fabricated id is
# not). Divergence is still DETECTED deterministically at submit -- see the
# unknown-id line in host_ac.build_host_ac_section.
_HOST_AC_JOB_DIRECTIVE = (
    "\n\n## Acceptance Criteria (SUPPLIED to you -- populate requirement_id)\n"
    "This ticket carries no acceptance criteria of its own. ONE list of derived "
    "criteria, numbered AC-001, AC-002, ..., is produced ONCE for this run (step "
    "0b of `jobs_to_run`) and supplied to you alongside this prompt. Set each "
    "test case's `requirement_id` to the id from THAT list which the case "
    "primarily validates, or JSON null when none applies.\n"
    "Do NOT derive your own list, do NOT renumber, and do NOT invent an AC id. "
    "If you are the SAME model that ran step 0b (no parallel fan-out), use the "
    "list you produced there -- do not derive a second, different one now. "
    "If no such list appears anywhere in your input, leave every "
    "`requirement_id` null: ids invented per category collide across the merged "
    "suite and re-point each other's traceability, which is worse than no "
    "traceability at all.\n"
)


@dataclass
class PreparedGeneration:
    """Everything the 8-category fan-out and the finalize half both need,
    computed once by ``_prepare_generation``.

    Carrying these as a dataclass lets ``generate_test_scenarios`` (server mode)
    and -- from ops-3 -- host mode share ONE pipeline while the moved server-mode
    code stays byte-identical. Nothing here is edited relative to the values the
    pre-refactor inline body produced.
    """

    user_msg: str
    rtm_hint: str
    feature_text: str
    complexity_text: str
    acs: list[AcceptanceCriterion]
    source_acs: list[AcceptanceCriterion]
    checklist_items: list[ChecklistItem]
    checklist_presented_ids: object
    checklist_audit: dict
    rule_packs: RulePackResult
    ui_content: dict | None
    parent_context: str
    cache_prefix_warm: bool
    jira_image_text: str
    attached_image_text: str
    jira_context_text: str
    image_notice: str
    # Populated for ops-3 host mode (the boomerang tools hand the category
    # specs and the response schema to the tester's own chat model). Server
    # mode reads neither -- its fan-out uses the CATEGORIES global directly.
    categories: list[tuple[str, str, str]]
    category_response_schema: dict
    # The TARGET ticket's own description -- no comment thread, no parent story,
    # no RAG or web-search blocks. Carried explicitly because the grounding checks
    # used to re-derive their source by slicing the assembled prompt, which (a)
    # handed them Jira COMMENT text, so a commenter could plant data-field rows or
    # fake ticket defects, and (b) discarded every prompt block that follows the
    # parent-story heading. Defaults to "" so an older prep record still loads.
    target_description: str = ""
