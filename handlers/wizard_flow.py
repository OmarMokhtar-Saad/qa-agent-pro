"""Wizard entry flow: the top-level menu, its routes and the handoffs."""

from __future__ import annotations

import hashlib
import logging
from typing import NamedTuple, cast

from handlers import _common, _elicit, explore_bug
from handlers.wizard_guided import guided_test_cases
from handlers.wizard_request import WizardIO

logger = logging.getLogger("tools.mcp_handlers")


class _Route(NamedTuple):
    """The tester's top-level choice plus the options it was picked from."""

    choice: str
    options: list[str]


# --------------------------------------------------------------------------- #
# Guided wizard (choice-driven UX; markdown fallback when elicitation is off)  #
# --------------------------------------------------------------------------- #

# Each branch maps a menu option to the tool the client should call
# next and the free-text parameter it still needs (choices are elicited; free
# text is not).
_WIZARD_HANDOFFS = {
    "Generate test cases": (
        "qa_generate_test_cases",
        "Send the feature description or a Jira/issue URL as `feature_or_url`.",
    ),
    "Report a bug": (
        "qa_bug_report",
        "Describe the bug in plain language as `description`.",
    ),
    "Exploratory testing coach": (
        "qa_explore_step",
        "Send the `feature` to explore and a stable `session_id` to keep coverage.",
    ),
}


def _wizard_options() -> list:
    """Top-level menu, mirroring the Chainlit starters. Feature analysis is not
    a separate entry: like Chainlit's "Test cases" starter, the generate branch
    asks for the source (describe / Jira / mobile screens / Jira + mobile) and
    produces the full suite with the Feature Analysis report. The direct
    qa_feature_analysis tool remains for technical, report-only use."""
    return [
        "Generate test cases",
        "Report a bug",
        "Exploratory testing coach",
    ]


def _wizard_menu_markdown(options: list) -> str:
    lines = [
        "## QA wizard",
        "",
        "Pick what you'd like to do, then call the matching tool:",
        "",
    ]
    for opt in options:
        tool, hint = _WIZARD_HANDOFFS[opt]
        lines.append(f"- **{opt}** — call `{tool}`. {hint}")
    return "\n".join(lines)


def _wizard_handoff_markdown(choice: str) -> str:
    tool, hint = _WIZARD_HANDOFFS[choice]
    return f"## {choice}\n\nGreat — call `{tool}` next. {hint}"


async def _wizard_route(route: _Route, io: WizardIO) -> str:
    """Run the flow the tester chose, or the handoff or menu for it."""
    choice = route.choice
    if choice == "Generate test cases":
        return await guided_test_cases(io)

    if choice == "Report a bug":
        return await _wizard_report_bug(choice, io.ask_text, io.progress)

    if choice == "Exploratory testing coach":
        return await _wizard_explore(choice, io.ask_text, io.progress)

    if choice in _WIZARD_HANDOFFS:
        return _wizard_handoff_markdown(choice)
    # Unrecognised selection — fall back to the full menu.
    return _wizard_menu_markdown(route.options)


async def _wizard_report_bug(
    choice: str, ask_text: _elicit.AskCb, progress: _common.ProgressCb
) -> str:
    """The wizard's bug-report flow: ask for the bug, then prepare the report."""
    asked = await _elicit._elicit_text(ask_text, "Describe the bug in plain language:")
    if asked.status == _common.CHOSEN and (asked.value or "").strip():
        # PREPARE half only (host-boomerang Phase 2): qa_bug_report hands
        # back a task envelope, so the wizard must name the SUBMIT tool
        # as the immediate next call or the open task is abandoned.
        prepared = await explore_bug.handle_bug_report(
            cast("str", asked.value).strip(), progress=progress
        )
        return prepared + (
            "\n\n_Next: write the report exactly as the task above "
            "asks, then call `qa_submit_bug_report` with its `task_id`._"
        )
    if asked.status == _common.DECLINED:
        return "👍 Cancelled."
    return _wizard_handoff_markdown(choice)


# Deterministic id: running the wizard again with the same
# feature continues the same coaching session.
def _wizard_session_id(feature: str) -> str:
    return "wizard-" + hashlib.sha1(feature.lower().encode("utf-8")).hexdigest()[:10]


# PREPARE half only (host-boomerang Phase 2): qa_explore_step no
# longer returns a coaching turn, so qa_submit_explore_step --
# not qa_wizard -- is the immediate next call.
def _explore_next_note(session_id: str) -> str:
    return (
        "\n\n_Next: write the coaching step the task above asks "
        "for and call `qa_submit_explore_step` with its `task_id`. "
        "After that, run `qa_wizard` again with the same feature, "
        f"or call `qa_explore_step` with session_id `{session_id}` "
        "and your `tester_response`._"
    )


async def _wizard_explore(
    choice: str, ask_text: _elicit.AskCb, progress: _common.ProgressCb
) -> str:
    """The wizard's exploratory flow: ask for the feature, then prepare a step."""
    asked = await _elicit._elicit_text(
        ask_text, "Which feature or area do you want to explore?"
    )
    if asked.status == _common.CHOSEN and (asked.value or "").strip():
        feature = cast("str", asked.value).strip()
        session_id = _wizard_session_id(feature)
        step = await explore_bug.handle_explore_step(
            feature, session_id, explore_bug.ExploreStepOpts(progress=progress)
        )
        return step + _explore_next_note(session_id)
    if asked.status == _common.DECLINED:
        return "👍 Cancelled."
    return _wizard_handoff_markdown(choice)


# F12 (2026-08-30): a DECLINED choice is not always a tester saying
# no. In Cursor the wizard returned this cancellation on a turn where
# `qa_generate_test_cases` correctly rendered its markdown menu, so
# the documented entry point for non-technical testers dead-ended on
# the very client the session ran in. Show the menu either way: a
# tester who really did cancel loses nothing by being shown what they
# declined, while one whose client never rendered a dialog now has
# something to act on.
def _declined_menu(options: list) -> str:
    return (
        "👍 Nothing selected — here is the menu, or call "
        "`qa_wizard` again anytime.\n\n" + _wizard_menu_markdown(options)
    )


async def run_wizard(io: WizardIO) -> str:
    await _common.audit("mcp_wizard")
    try:
        options = _wizard_options()
        top = await _elicit.elicit_choice(
            io.choose, "What would you like to do?", options
        )
        if top.status == _common.UNAVAILABLE:
            return _wizard_menu_markdown(options)
        if top.status == _common.DECLINED:
            return _declined_menu(options)
        return await _wizard_route(_Route(cast("str", top.value), options), io)
    except Exception as exc:
        logger.exception("handle_wizard failed")
        return f"⚠️ Wizard failed: {exc}"
