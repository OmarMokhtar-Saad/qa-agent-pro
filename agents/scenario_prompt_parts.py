"""Prompt blocks of a prepared generation: Jira, source docs, rule packs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents.scenario_html import _strip_html
from agents.scenario_ui_block import _build_ui_prompt_block
from tools import settings_port
from tools.rule_packs import PackInputs, build_rule_packs
from tools.untrusted import wrap_untrusted

if TYPE_CHECKING:
    from agents.scenario_args import RulePackInputs
    from agents.scenario_source import _SourceScope


def _context_block(jira_context_text: str) -> list[str]:
    """The ticket's feature-documentation block, when it has any text."""
    if not jira_context_text:
        return []
    limit = settings_port.get().jira_max_context_chars or 12000
    return [
        "## Feature Documentation\n"
        + wrap_untrusted("jira_or_web_content", jira_context_text, limit=limit)
    ]


def _ac_block(raw_ac_text: str) -> list[str]:
    """The ticket's acceptance-criteria block, when it has any text."""
    if not raw_ac_text:
        return []
    limit = settings_port.get().jira_max_ac_chars or 6000
    return [
        "## Acceptance Criteria\n"
        + wrap_untrusted("jira_acceptance_criteria", raw_ac_text, limit=limit)
    ]


def _parent_block(parent_context: str) -> list[str]:
    """The parent-story background block, only when a parent exists."""
    if not parent_context:
        return []
    limit = settings_port.get().jira_max_parent_chars
    return [
        "## Parent Story (BACKGROUND ONLY — do not test this directly)\n"
        + wrap_untrusted("jira_parent_story", parent_context, limit=limit)
    ]


# Jira blocks: (parts, jira_context_text, target_description, has_jira_images).
#
# No ticket, or an errored one, gives ``([], "", feature_text or "", False)``:
# the pasted-description path grounds on the tester's own text. A ticket's
# target_description is its DESCRIPTION only (raw_text carries the comment
# thread), so an empty description gives "" -- never feature_text.
# Caps go TO wrap_untrusted (2026-08-31, F1/F2): slicing first defeated its
# "...[truncated]" marker and cut tickets silently. The parent story keeps its
# OWN label and appears only when a parent exists, so a parentless prompt is
# byte-identical (the containment test counts untrusted blocks). Ticket images
# ride to the host's own model (IMAGE_JOB); only their presence is recorded.
def _jira_prompt_parts(
    url_content: dict | None, scope: _SourceScope
) -> tuple[list[str], str, str, bool]:
    """Jira blocks: (parts, jira_context_text, target_description, has_jira_images)."""
    if not url_content or url_content.get("error"):
        return [], "", scope.feature_text or "", False
    jira_context_text = _strip_html(
        url_content.get("raw_text", "") or url_content.get("description", "")
    )
    target_description = _strip_html(url_content.get("description", "") or "")
    parts = (
        _context_block(jira_context_text)
        + _ac_block(_raw_ac_text(url_content))
        + _parent_block(scope.parent_context)
    )
    has_jira_images = bool(url_content.get("images"))
    return parts, jira_context_text, target_description, has_jira_images


def _spec_block(spec_text: str | None) -> list[str]:
    """The pasted requirements/spec document, when it has any text."""
    if not (spec_text and spec_text.strip()):
        return []
    return [
        "## Requirements / Spec Document\n"
        + wrap_untrusted("spec_document", spec_text, limit=20_000)
    ]


def _openapi_block(openapi_text: str | None) -> list[str]:
    """The OpenAPI/Swagger block, when it has any text."""
    if not (openapi_text and openapi_text.strip()):
        return []
    return [
        "## API Specification (OpenAPI/Swagger)\n"
        + wrap_untrusted("openapi_spec", openapi_text[:12000])
    ]


def _live_ui_block(ui_content: dict | None) -> list[str]:
    """The live-UI structure block, when the extract is usable."""
    if not ui_content or ui_content.get("error"):
        return []
    ui_block = _build_ui_prompt_block(ui_content)
    return [wrap_untrusted("live_ui_structure", ui_block)] if ui_block else []


# Untrusted spec, OpenAPI and live-UI blocks, in that order.
#
# The spec cap (20_000) is INLINED: settings.qa_max_spec_chars was deleted in
# batch D1 (2026-08-15) with tools/doc_ingest.py, spec_text's only producer,
# so the block is latent and a revived producer inherits the same bound.
def _source_doc_parts(
    spec_text: str | None, openapi_text: str | None, ui_content: dict | None
) -> list[str]:
    """Untrusted spec, OpenAPI and live-UI blocks, in that order."""
    return (
        _spec_block(spec_text)
        + _openapi_block(openapi_text)
        + _live_ui_block(ui_content)
    )


def _raw_ac_text(url_content: dict | None) -> str:
    """The ticket's acceptance-criteria text with markup stripped."""
    if not url_content or url_content.get("error"):
        return ""
    return _strip_html(url_content.get("acceptance_criteria", "") or "")


# (user_msg, jira_context_text, target_description, has_jira_images).
#
# The TARGET goes LAST. Everything above it -- parent story, RAG, compliance,
# images, spec, OpenAPI, live UI -- is background, and recency is the strongest
# position in a long prompt, so the one thing this suite must actually cover is
# the last SUBJECT the model reads. Load-bearing for a Jira sub-task, whose
# parent BACKGROUND block is far longer than the target itself.
def _build_user_prompt(
    url_content: dict | None, scope: _SourceScope, doc_parts: list[str]
) -> tuple[str, str, str, bool]:
    """(user_msg, jira_context_text, target_description, has_jira_images)."""
    parts, jira_context_text, target_description, has_jira_images = _jira_prompt_parts(
        url_content, scope
    )
    parts += doc_parts
    parts.append(
        "## Feature to Test\n"
        + wrap_untrusted("feature_description", scope.feature_text)
    )
    return "\n\n".join(parts), jira_context_text, target_description, has_jira_images


# Batch 3 rule packs: pure, synchronous, zero LLM calls, inert when OFF.
#
# jira_context_text (not feature_text) is the haystack: the EN/AR message table
# lives in the ticket BODY, while feature_text is a one-line title for a Jira
# URL input. parent_context rides along as extra body text for pair extraction
# ONLY -- the TICKET-7154 separation is preserved: nothing from it is merged into
# feature_text or raw_text and it never becomes a navigation target.
#
# The enforcement seam that interleaved the packs' mandated lines into the
# atomic checklist is GONE (dead-code deletion 3a, 2026-08-16): the packs are
# hardcoded OFF and prepare's checklist is always empty, so flipping a pack's
# seam back on no longer restores a checklist-enforcement tier. A revived pack
# still reaches the generator through format_rule_pack_prompt_block.
def _prompt_rule_packs(scope: _SourceScope, inputs: RulePackInputs):
    """Batch 3 rule packs: pure, synchronous, zero LLM calls, inert when OFF."""
    return build_rule_packs(
        PackInputs(
            scope.feature_text,
            jira_text="\n".join(
                t for t in (inputs.jira_context_text, scope.parent_context) if t
            ),
            ui_content=inputs.ui_content,
            openapi_text=inputs.openapi_text or "",
            images_present=inputs.images_present,
            source_ref=scope.source_url or (scope.feature_text or "")[:80],
        )
    )
