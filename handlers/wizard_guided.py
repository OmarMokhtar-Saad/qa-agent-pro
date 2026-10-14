"""Wizard guided source picker for test-case generation."""

from __future__ import annotations

from typing import cast

from handlers import _common, _elicit
from handlers.wizard_capture import _guided_capture_images
from handlers.wizard_request import GenerateRequest, WizardIO
from handlers.wizard_sources import (
    _TC_SOURCE_PROMPTS,
    _tc_source_menu_markdown,
    _tc_sources,
)


async def _typed_feature_retry(io: WizardIO) -> tuple[bool, str]:
    """Ask for the feature as free text; (generated, the generate reply)."""
    asked = await _elicit._elicit_text(
        io.ask_text,
        "Describe the feature to test (or paste a Jira / web / Swagger URL):",
    )
    if asked.status == _common.CHOSEN and (asked.value or "").strip():
        request = GenerateRequest(progress=io.progress)
        return True, await io.generate(cast("str", asked.value).strip(), request)
    return False, ""


async def _declined_choice(io: WizardIO) -> tuple[str | None, str]:
    # Some clients (Cursor 3.12) auto-cancel enum dialogs but still render
    # free-text prompts — try one before degrading to the menu. Never a
    # dead end either way.
    done, reply = await _typed_feature_retry(io)
    if done:
        return reply, ""
    return (
        "ℹ️ The picker dialog was dismissed — no problem, here are the "
        "options:\n\n" + _tc_source_menu_markdown(),
        "",
    )


async def _no_dialog_choice(io: WizardIO) -> tuple[str | None, str]:
    # No choice dialogs — degrade to the simple typed-feature path.
    done, reply = await _typed_feature_retry(io)
    if done:
        return reply, ""
    return _tc_source_menu_markdown(), ""


async def _guided_source_choice(io: WizardIO) -> tuple[str | None, str]:
    """Ask where the feature comes from; (reply to return or None, chosen source)."""
    sources = _tc_sources()
    source = await _elicit.elicit_choice(
        io.choose, "Where is the feature coming from?", list(sources)
    )
    if source.status == _common.DECLINED:
        return await _declined_choice(io)
    src = sources.get(source.value or "", "")
    if source.status != _common.CHOSEN or not src:
        return await _no_dialog_choice(io)
    return None, src


async def _source_text(src: str, io: WizardIO) -> str | None:
    """The typed input of a source, "" when it needs none, None when unanswered."""
    if src not in _TC_SOURCE_PROMPTS:
        return ""
    asked = await _elicit._elicit_text(io.ask_text, _TC_SOURCE_PROMPTS[src])
    if asked.status != _common.CHOSEN or not (asked.value or "").strip():
        return None
    return cast("str", asked.value).strip()


# The Feature Analysis wizard generates the full suite for every source.
# It used to pass force_feature_report=True as well; that argument stopped
# reaching anything at 41e0ec5 (which deleted the server fall-through that
# forwarded it) and the parameter was deleted on 2026-08-16 with the report
# branch itself (P2-E3). The tester still gets a report -- from
# qa_feature_analysis / qa_submit_feature_analysis, which are chat-only.
async def guided_test_cases(io: WizardIO) -> str:
    reply, src = await _guided_source_choice(io)
    if reply is not None:
        return reply
    text = await _source_text(src, io)
    if text is None:
        return _tc_source_menu_markdown()
    refusal, images = await _guided_capture_images(src, io)
    if refusal is not None:
        return refusal
    request = GenerateRequest(
        attached_images=images or None,
        choose=io.choose,
        ask_text=io.ask_text,
        progress=io.progress,
    )
    return await io.generate(
        text or "Feature captured from mobile device screens.", request
    )
