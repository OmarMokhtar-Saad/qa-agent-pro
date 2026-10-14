"""Moved from handlers (refactor P2): wizard."""

from __future__ import annotations

import logging

from handlers import (  # noqa: F401
    _common,
    _elicit,
    capture,
    explore_bug,
    generate_prepare,
)
from handlers.wizard_capture import _guided_capture_images  # noqa: F401
from handlers.wizard_flow import (  # noqa: F401
    _WIZARD_HANDOFFS,
    _wizard_explore,
    _wizard_handoff_markdown,
    _wizard_menu_markdown,
    _wizard_options,
    _wizard_report_bug,
    _wizard_route,
    run_wizard,
)
from handlers.wizard_guided import (  # noqa: F401
    _guided_source_choice,
    guided_test_cases,
)
from handlers.wizard_request import GenerateRequest, WizardIO, _prepare_kwargs
from handlers.wizard_sources import (  # noqa: F401
    _TC_COUNT_WORDS,
    _TC_FULL_EDITION_SOURCES,
    _TC_SOURCE_LABELS,
    _TC_SOURCE_MENU_LINES,
    _TC_SOURCE_PROMPTS,
    _tc_source_menu_markdown,
    _tc_sources,
)
from tools import dispatch_guard

logger = logging.getLogger("tools.mcp_handlers")


# chat model, exactly as qa_prepare_test_cases does. qa_generate_test_cases
# returns str, so render_prepare_payload's self-contained markdown+JSON block is
# what even a string-only client relays.
#
# The four gate arguments MUST be forwarded: the image gate lives in
# handle_prepare_test_cases and this tool is how most testers reach it, so
# without them the gate would ask a question this tool has no parameter to
# answer, and the only escape on a client that auto-cancels dialogs would be
# to call a DIFFERENT tool. attached_images is forwarded for a reason this
# reroute once got wrong by DROPPING it: the Feature-Analysis `jira_mobile`
# route captures device screens and calls this handler with them, so without
# it the screens vanished here AND beat 1 asked the tester where the screens
# come from immediately after they captured them.
#
# 2026-08-15 (dead-code deletion Phase 2, batch P2-D): the reroute used to
# sit under two guards -- `not attached_images or _host_image_forwarding_on()`
# and `llm.resolve_generation_mode() == "host"` -- with a legacy SERVER-mode
# branch below them. Both guards resolve from the hardcoded "host" constant,
# so that branch had been unreachable since 2026-08-12 and is now DELETED,
# and the guards went with it. They are deliberately not kept as a
# "harmless" wrapper: with nothing below, `if <constant>: return ...` falls
# off the end and returns None, and re-adding an else is how a server-side
# call gets back onto a tester-facing tool. The deleted branch called
# ground_and_gate WITHOUT run_ambiguity_llm, i.e. at its dangerous default
# (True), which would have reached
# requirement_analyzer.analyze_requirements -- a server-side backend call.
# That is what makes deleting the whole fall-through, rather than only its
# guard, the safe direction. (ui_extractor's Tier-3 vision fallback was the
# other such call; P2-F1 deleted it outright on 2026-08-16.)
async def handle_generate_test_cases(
    feature_or_url: str, request: GenerateRequest | None = None
) -> str:
    dispatch_guard.require_dispatched("qa_generate_test_cases")
    text = (feature_or_url or "").strip()
    req = request or GenerateRequest()
    if not text:
        # No source given — run the guided picker (dialogs where the client
        # supports elicitation, markdown menu otherwise) instead of erroring.
        return await _guided_test_cases(
            choose=req.choose, ask_text=req.ask_text, progress=req.progress
        )
    return generate_prepare.render_prepare_payload(
        await generate_prepare.handle_prepare_test_cases(text, **_prepare_kwargs(req))
    )


def _wizard_io(
    choose: _common.ChooseCb, ask_text: _elicit.AskCb, progress: _common.ProgressCb
) -> WizardIO:
    # The generate handler is read from this module at call time so a patch
    # of handle_generate_test_cases bites for both wizard entry points.
    return WizardIO(choose, ask_text, progress, handle_generate_test_cases)


async def _guided_test_cases(
    *,
    choose: _common.ChooseCb = None,
    ask_text: _elicit.AskCb = None,
    progress: _common.ProgressCb = None,
) -> str:
    """Guided source picker for test-case generation (Chainlit-starter parity).

    Asks where the feature comes from — describe / Jira / web URL / Swagger
    link / mobile screens / Jira + mobile — collects the missing input, and
    runs the full generation. Dialogs where the client supports elicitation;
    a markdown menu otherwise. Never raises."""
    return await guided_test_cases(_wizard_io(choose, ask_text, progress))


async def handle_wizard(
    *,
    choose: _common.ChooseCb = None,
    ask_text: _elicit.AskCb = None,
    progress: _common.ProgressCb = None,
) -> str:
    """Entry-point guided menu mirroring the Chainlit starters.

    With full elicitation (choice dialogs + free-text input) the wizard runs
    the chosen flow END-TO-END: it collects the missing input, invokes the real
    handler, and returns the finished result — a non-technical tester never
    needs tool names or parameters. Without free-text support it degrades to
    the concise handoff telling the client which tool to call next; without
    any elicitation it returns the markdown menu. Never raises."""
    return await run_wizard(_wizard_io(choose, ask_text, progress))
