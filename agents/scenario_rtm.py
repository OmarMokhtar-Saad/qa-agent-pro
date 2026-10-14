"""Acceptance-criteria sourcing and the RTM hint of a prepared generation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agents.scenario_prepared import _HOST_AC_JOB_DIRECTIVE
from tools.jira_mcp import _extract_ac_from_description
from tools.rtm import (
    AcceptanceCriterion,
    format_ac_prompt_block,
    parse_acceptance_criteria,
)
from tools.rule_packs import format_rule_pack_prompt_block

if TYPE_CHECKING:
    from agents.scenario_args import RtmFacts
    from agents.scenario_source import _SourceScope

logger = logging.getLogger("agents.test_scenario_agent")


def _jira_source_acs(url_content: dict | None) -> list[AcceptanceCriterion]:
    """Criteria parsed from a fetched ticket's acceptance-criteria text."""
    if not url_content or url_content.get("error"):
        return []
    acs = parse_acceptance_criteria(url_content.get("acceptance_criteria", "") or "")
    if acs:
        logger.info("Parsed %d acceptance criteria for RTM", len(acs))
    return acs


# PASTED-TEXT path: a pasted feature carrying its own "Acceptance Criteria"
# heading has WRITTEN source criteria. Same parser as the Jira path, over
# ``stripped_feature`` (the text exactly as pasted, never the rewritten
# feature_text).
def _pasted_source_acs(stripped_feature: str) -> list[AcceptanceCriterion]:
    """Criteria written into a pasted feature text under its own heading."""
    pasted_ac = _extract_ac_from_description(stripped_feature)
    acs = parse_acceptance_criteria(pasted_ac) if pasted_ac else []
    if acs:
        logger.info(
            "Parsed %d acceptance criteria from the pasted feature text", len(acs)
        )
    return acs


# AC sourcing phase of ``_prepare_generation``.
#
# Returns ``(acs, source_acs, need_acs, host_ac_job)``. ``source_acs`` holds
# only REAL, source-parsed criteria (empty when the host must derive them),
# the one ground truth the AC-anchoring check may anchor against. When no
# criteria exist the HOST derives them (``agents.host_ac.AC_JOB``); there is
# no server-side synthesis, so ``host_ac_job`` always equals ``need_acs``.
def _source_acs(
    url_content: dict | None, stripped_feature: str, feature_text: str
) -> tuple[list[AcceptanceCriterion], list[AcceptanceCriterion], bool, bool]:
    """AC sourcing phase of ``_prepare_generation``."""
    acs = _jira_source_acs(url_content)
    # Gated on ``not url_content`` so the Jira path is untouched in both directions.
    if not acs and not url_content:
        acs = _pasted_source_acs(stripped_feature)
    need_acs = not acs and bool(feature_text and feature_text.strip())
    return acs, list(acs), need_acs, need_acs


# The hint injected into every category system prompt.
#
# The AC block, the source-URL scope directive (never navigate to the Jira
# link) and the parent scope directive come first; the rule-pack clause is
# appended after them. It carries only code constants, never untrusted text.
# The host AC directive is last, and only when the ticket carried no criteria
# at all, so the HOST derives them.
def _build_rtm_hint(
    facts: RtmFacts, nav_scope_directive: str, parent_scope_directive: str
) -> str:
    """The hint injected into every category system prompt."""
    rtm_hint = (
        format_ac_prompt_block(facts.acs) + nav_scope_directive + parent_scope_directive
    )
    rtm_hint = rtm_hint + format_rule_pack_prompt_block(facts.rule_packs)
    if facts.host_ac_job:
        rtm_hint = rtm_hint + _HOST_AC_JOB_DIRECTIVE
    return rtm_hint


def _complexity_text(feature_text: str, single_screen: bool) -> str:
    """The text the case-count bounds read, never the scoped description.

    Case-count bounds read the ORIGINAL text: the scoped description is
    deliberately verbose. single_screen uses a short NON-EMPTY proxy because
    _generate_for_category falls back to feature_text when this is falsy.
    """
    return "single mobile screen" if single_screen else feature_text


def _scope_rtm_hint(facts: RtmFacts, scope: _SourceScope) -> str:
    """``_build_rtm_hint`` fed from a resolved ``_SourceScope``."""
    return _build_rtm_hint(
        facts, scope.nav_scope_directive, scope.parent_scope_directive
    )
