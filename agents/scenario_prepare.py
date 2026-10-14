"""Assembly of a PreparedGeneration from the source, prompt and AC phases."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from agents.scenario_args import GenerationRequest, RtmFacts, RulePackInputs
from agents.scenario_categories import effective_categories
from agents.scenario_prepared import PreparedGeneration
from agents.scenario_prompt_parts import (
    _build_user_prompt,
    _prompt_rule_packs,
    _source_doc_parts,
)
from agents.scenario_prompt_rules import _category_response_model
from agents.scenario_rtm import _complexity_text, _scope_rtm_hint, _source_acs
from agents.scenario_source import (
    _resolve_source_scope,
    _SourceScope,
    _unreadable_source_refusal,
)

if TYPE_CHECKING:
    from tools.rule_packs import RulePackResult


class _Prompt(NamedTuple):
    """The scoped source and the user message built from it."""

    scope: _SourceScope
    user_msg: str
    jira_context_text: str
    target_description: str
    has_jira_images: bool


def _prompt_stage(src: GenerationRequest) -> _Prompt:
    """Resolve the scoped source, then build the user message from it."""
    scope = _resolve_source_scope(src.feature_text, src.url_content, src.ui_content)
    docs = _source_doc_parts(src.spec_text, src.openapi_text, src.ui_content)
    return _Prompt(scope, *_build_user_prompt(src.url_content, scope, docs))


def _packs_for(src: GenerationRequest, p: _Prompt, attached: bool) -> RulePackResult:
    """Rule packs for the prompt; attached or ticket images light images_present."""
    return _prompt_rule_packs(
        p.scope,
        RulePackInputs(
            p.jira_context_text,
            src.ui_content,
            src.openapi_text,
            p.has_jira_images or attached,
        ),
    )


def _prompt_fields(p: _Prompt) -> dict[str, Any]:
    """The PreparedGeneration fields that come straight from the prompt stage."""
    return {
        "user_msg": p.user_msg,
        "feature_text": p.scope.feature_text,
        "parent_context": p.scope.parent_context,
        "jira_context_text": p.jira_context_text,
        "target_description": p.target_description,
    }


def _ac_fields(
    src: GenerationRequest, p: _Prompt, packs: RulePackResult
) -> dict[str, Any]:
    """Acceptance criteria and the RTM hint built from them."""
    acs, source_acs, _, host_ac_job = _source_acs(
        src.url_content, (src.feature_text or "").strip(), p.scope.feature_text
    )
    hint = _scope_rtm_hint(RtmFacts(acs, packs, host_ac_job), p.scope)
    return {"acs": acs, "source_acs": source_acs, "rtm_hint": hint}


def _assembled(src: GenerationRequest) -> PreparedGeneration:
    """The PreparedGeneration for a source that was readable."""
    prompt = _prompt_stage(src)
    packs = _packs_for(src, prompt, bool(src.attached_images))
    return _prepared_generation(
        **_prompt_fields(prompt),
        **_ac_fields(src, prompt, packs),
        rule_packs=packs,
        ui_content=src.ui_content,
        complexity_text=_complexity_text(src.feature_text, src.single_screen),
    )


# Assemble the prompt, RTM hint and rule packs every category job shares.
#
# Returns a PreparedGeneration, or a (message, "", "", "", "error") tuple when
# the source URL could not be read and no real feature text was supplied.
# attached_images (tester screenshots) ride to the host's own multimodal model.
async def _prepare_generation(
    req: GenerationRequest,
) -> PreparedGeneration | tuple[str, str, str, str, str]:
    """Assemble the prompt, RTM hint and rule packs every category job shares."""
    refusal = _unreadable_source_refusal(req.url_content, req.feature_text)
    if refusal:
        return refusal
    return _assembled(req)


# PreparedGeneration from the computed fields; the constant fields live here.
#
# * checklist_items / checklist_presented_ids / checklist_audit stay empty for
#   the whole of prepare: the atomic checklist is derived by the tester's OWN
#   model (agents.host_checklist.CHECKLIST_JOB) and arrives on the SUBMISSION, where
#   handlers/ validates it (and runs the granularity audit) and
#   sets prepared.checklist_items. The fields stay so nothing reads them by
#   ABSENCE; mcp_handlers reads checklist_presented_ids back on the submit side.
# * jira_image_text / attached_image_text / image_notice are always "": the
#   server-side vision calls were deleted on 2026-08-16 (P2-F1); raw images ride
#   to the host's own multimodal model, so nothing was lost and no "configure
#   ANTHROPIC_API_KEY for vision" notice would be true.
# * cache_prefix_warm is permanently False (the prompt-cache warm-up was deleted
#   with dead-code batch P2-F2): "send UNMARKED prompts", i.e. today's cost.
def _prepared_generation(**live: Any) -> PreparedGeneration:
    """PreparedGeneration from the computed fields; the constant fields live here."""
    return PreparedGeneration(
        checklist_items=[],
        checklist_presented_ids=[],
        checklist_audit={},
        cache_prefix_warm=False,
        jira_image_text="",
        attached_image_text="",
        image_notice="",
        categories=effective_categories(),
        category_response_schema=_category_response_model().model_json_schema(),
        **live,
    )
