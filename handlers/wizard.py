"""Moved from handlers (refactor P2): wizard."""

from __future__ import annotations

import hashlib
import logging

from handlers import _common, _elicit, capture, explore_bug, generate_prepare
from tools import dispatch_guard

logger = logging.getLogger("tools.mcp_handlers")


# handle_generate_test_cases: generation is CHAT-ONLY and UNCONDITIONAL: the
# tool grounds the request and hands the 8-category fan-out to the tester's OWN
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
# _ground_and_gate WITHOUT run_ambiguity_llm, i.e. at its dangerous default
# (True), which would have reached
# requirement_analyzer.analyze_requirements -- a server-side backend call.
# That is what makes deleting the whole fall-through, rather than only its
# guard, the safe direction. (ui_extractor's Tier-3 vision fallback was the
# other such call; P2-F1 deleted it outright on 2026-08-16.)
async def handle_generate_test_cases(
    feature_or_url: str,
    *,
    attached_images: list | None = None,
    proceed_anyway: bool = False,
    source_plan: str = "",
    attached_image_count: int = 0,
    capture_ids: list | None = None,
    image_gate_ack: bool = False,
    image_carry_ack: bool = False,
    choose: _common.ChooseCb = None,
    ask_text: _elicit.AskCb = None,
    progress: _common.ProgressCb = None,
    jira_content_json: str = "",
    stage_token: str = "",
) -> str:
    dispatch_guard.require_dispatched("qa_generate_test_cases")
    text = (feature_or_url or "").strip()
    if not text:
        # No source given — run the guided picker (dialogs where the client
        # supports elicitation, markdown menu otherwise) instead of erroring.
        return await _guided_test_cases(
            choose=choose, ask_text=ask_text, progress=progress
        )
    return generate_prepare.render_prepare_payload(
        await generate_prepare.handle_prepare_test_cases(
            text,
            attached_images=attached_images,
            proceed_anyway=proceed_anyway,
            choose=choose,
            ask_text=ask_text,
            progress=progress,
            jira_content_json=jira_content_json,
            stage_token=stage_token,
            source_plan=source_plan,
            attached_image_count=attached_image_count,
            capture_ids=list(capture_ids or []),
            image_gate_ack=image_gate_ack,
            image_carry_ack=image_carry_ack,
        )
    )


# Sources offered by the wizard's "Generate test cases" branch — mirrors the
# Chainlit "Test cases" starter, which routes through the Feature Analysis
# wizard's Jira / Mobile / Jira + Mobile menu before generating the suite.
_TC_SOURCE_LABELS = {
    "Describe the feature": "describe",
    "From a Jira ticket": "jira",
    "From a web page URL": "web",
    "From a Swagger/OpenAPI link": "swagger",
    "From mobile screens": "mobile",
    "Jira + mobile screens": "jira_mobile",
}

_TC_SOURCE_PROMPTS = {
    "describe": "Describe the feature to test:",
    "jira": "Paste the Jira/issue URL (or describe the feature):",
    "jira_mobile": "Paste the Jira/issue URL (or describe the feature):",
    "web": "Paste the web page URL:",
    "swagger": "Paste the Swagger/OpenAPI spec URL:",
}

# The two screen-capture sources are the ONLY ones that route to a tool this
# server does not always register: both call `qa_feature_analysis` and then
# `qa_submit_feature_analysis`, which mcp_server registers inside
# `if not mcp_handlers._test_cases_only():`. The public distribution IS
# test-cases-only, so on it these two options told the client to call a tool
# that is not there, mid-flow, in front of a tester -- the identical silent
# failure mcp_server already guards for tools/guidance.py, which this menu
# escaped only because it lives here rather than in that module.
_TC_FULL_EDITION_SOURCES = ("mobile", "jira_mobile")

#: One menu line per source, keyed by the source id so the NUMBERS are derived
#: from the list actually emitted rather than written into the prose.
_TC_SOURCE_MENU_LINES = {
    "describe": "**Describe the feature** — user types it in plain language",
    "jira": "**Jira ticket** — user pastes the issue URL",
    "web": "**Web page** — user pastes the page URL (the live UI is read)",
    "swagger": "**Swagger/OpenAPI link** — user pastes the spec URL (API test cases)",
    "mobile": "**Mobile screens** — capture from a connected device",
    "jira_mobile": "**Jira + mobile screens** — merge the ticket with captured screens",
}

_TC_COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _tc_sources() -> dict:
    """The ``{label: source_id}`` map this EDITION can actually serve.

    The condition MIRRORS the registration site
    (``mcp_server``: ``if _feature_analysis_enabled() and not
    mcp_handlers._test_cases_only():``) rather than restating half of it. The
    edition check alone is not enough: both gates have to hold for the pair to
    exist, so a menu gated on the edition only goes stale again the moment the
    Feature-Analysis seam is turned off -- which is exactly what this module's
    own ``_feature_analysis_enabled`` seam is for. mcp_server's copy of that
    seam cannot be read from here (nothing in this direction may import the
    transport), and the two are documented as checked in both places.

    Note `qa_capture_screens` is registered on BOTH editions and is NOT what is
    gated here -- the missing tool is `qa_feature_analysis`, not device capture.
    """
    if _common._feature_analysis_enabled() and not _common._test_cases_only():
        return dict(_TC_SOURCE_LABELS)
    return {
        label: src
        for label, src in _TC_SOURCE_LABELS.items()
        if src not in _TC_FULL_EDITION_SOURCES
    }


def _tc_source_menu_markdown() -> str:
    """Fallback when the native dialog is unavailable or was auto-dismissed.

    Written as an instruction to the HOST assistant: present the options THIS
    edition can serve as a structured multiple-choice question (editors render
    those reliably, unlike MCP elicitation dialogs), then call back with the
    answer.

    The count word and the mapping paragraph are DERIVED from ``_tc_sources()``.
    The text says "EXACTLY these N options" and "Do not invent different
    options", so N has to move with the list or the instruction contradicts
    itself.

    **The options are keyed, not numbered** (2026-09-04), for the reason
    ``tools/mobile/render.py``'s module docstring gives: the host re-presents
    them in its own question UI, which relabels (a/b/c/d) and reorders, so a
    position addressed the wrong option. These numbers never round-tripped as
    an argument -- the host is told to call a tool directly -- but the mapping
    paragraph named options by position, and a reordering UI would have sent
    the wrong one to `mode=mobile`."""
    srcs = list(_tc_sources().values())
    count = _TC_COUNT_WORDS.get(len(srcs), str(len(srcs)))
    body = "".join(f"- `{s}` \u2014 {_TC_SOURCE_MENU_LINES[s]}\n" for s in srcs)
    typed = [s for s in srcs if s not in _TC_FULL_EDITION_SOURCES]
    mapping = (
        "After the user picks: for "
        + ", ".join(f"`{s}`" for s in typed)
        + " call `qa_generate_test_cases` with `feature_or_url` set to their "
        "text/URL"
    )
    if "mobile" in srcs:
        mapping += (
            "; for `mobile` call `qa_feature_analysis` with `mode=mobile`; for "
            "`jira_mobile` call `qa_feature_analysis` with `mode=jira_mobile`. "
            "`mobile` and `jira_mobile` are a TWO-step chat-only flow: "
            "`qa_feature_analysis` hands YOU a task envelope to answer, then "
            "you call `qa_submit_feature_analysis` with its `task_id` and your "
            "JSON."
        )
    else:
        mapping += "."
    return (
        "## Ask the user: where is the feature coming from?\n\n"
        f"Present EXACTLY these {count} options to the user as a "
        "multiple-choice question (use your ask-user/questions UI, not prose), "
        "then follow the mapping below. Do not invent different options. Your "
        "UI may relabel or reorder them; the key on each line is what "
        "identifies it.\n\n" + body + "\n" + mapping
    )


async def _guided_source_choice(
    choose: _common.ChooseCb, ask_text: _elicit.AskCb, progress: _common.ProgressCb
) -> tuple[str | None, str]:
    """Ask where the feature comes from; (reply to return or None, chosen source)."""
    sources = _tc_sources()
    source = await _elicit._elicit_choice(
        choose, "Where is the feature coming from?", list(sources)
    )
    if source.status == _common.DECLINED:
        # Some clients (Cursor 3.12) auto-cancel enum dialogs but still render
        # free-text prompts — try one before degrading to the menu. Never a
        # dead end either way.
        asked = await _elicit._elicit_text(
            ask_text,
            "Describe the feature to test (or paste a Jira / web / Swagger URL):",
        )
        if asked.status == _common.CHOSEN and (asked.value or "").strip():
            return (
                await handle_generate_test_cases(
                    asked.value.strip(), progress=progress
                ),
                "",
            )
        return (
            "ℹ️ The picker dialog was dismissed — no problem, here are the "
            "options:\n\n" + _tc_source_menu_markdown(),
            "",
        )
    src = sources.get(source.value or "", "")
    if source.status != _common.CHOSEN or not src:
        # No choice dialogs — degrade to the simple typed-feature path.
        asked = await _elicit._elicit_text(
            ask_text,
            "Describe the feature to test (or paste a Jira / web / Swagger URL):",
        )
        if asked.status == _common.CHOSEN and (asked.value or "").strip():
            return (
                await handle_generate_test_cases(
                    asked.value.strip(), progress=progress
                ),
                "",
            )
        return _tc_source_menu_markdown(), ""
    return None, src


async def _guided_capture_images(
    src: str, choose: _common.ChooseCb, progress: _common.ProgressCb
) -> tuple[str | None, list]:
    """Capture screens for the mobile sources; (refusal or None, images)."""
    if src not in ("mobile", "jira_mobile"):
        return None, []
    if not _common._mobile_capture():
        if src == "mobile":
            return "ℹ️ Mobile capture is disabled in this build.", []
        # jira_mobile continues from the ticket alone (parity with the
        # Chainlit wizard's capture-off fallback).
        return None, []
    picked_dev = await _elicit._elicit_device(choose)
    if picked_dev.status == _common.DECLINED:
        return "👍 Cancelled — no device selected.", []
    if _common._unanswered(picked_dev):
        # K1 (2026-08-10): this is dialog #3-4 of a qa_wizard call, so a
        # spent budget is its TYPICAL unanswered state, not an edge case.
        # The old text claimed no devices were connected -- but
        # _elicit_device only reaches a dialog AFTER list_devices returned
        # some, so that was simply untrue whenever the tester just did not
        # answer.
        return await capture._device_menu_markdown("qa_wizard"), []
    if picked_dev.status != _common.CHOSEN:
        return (
            "⚠️ No connected devices found. Attach a device or boot an "
            "emulator/simulator, then try again (check with "
            "`qa_list_devices`).",
            [],
        )
    device = await _common._resolve_device(picked_dev.value or "")
    if device is None:
        return "⚠️ Device not found — run qa_list_devices and retry.", []
    screens, capture_error = await capture._fa_capture_screens(
        device, choose=choose, progress=progress
    )
    if capture_error and not screens:
        return f"⚠️ Couldn't capture a screenshot: {capture_error}", []
    return None, screens


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
    reply, src = await _guided_source_choice(choose, ask_text, progress)
    if reply is not None:
        return reply
    text = ""
    if src in _TC_SOURCE_PROMPTS:
        asked = await _elicit._elicit_text(ask_text, _TC_SOURCE_PROMPTS[src])
        if asked.status != _common.CHOSEN or not (asked.value or "").strip():
            return _tc_source_menu_markdown()
        text = asked.value.strip()
    refusal, images = await _guided_capture_images(src, choose, progress)
    if refusal is not None:
        return refusal
    # The Feature Analysis wizard generates the full suite for every source.
    # It used to pass force_feature_report=True as well; that argument stopped
    # reaching anything at 41e0ec5 (which deleted the server fall-through that
    # forwarded it) and the parameter was deleted on 2026-08-16 with the report
    # branch itself (P2-E3). The tester still gets a report -- from
    # qa_feature_analysis / qa_submit_feature_analysis, which are chat-only.
    return await handle_generate_test_cases(
        text or "Feature captured from mobile device screens.",
        attached_images=images or None,
        choose=choose,
        ask_text=ask_text,
        progress=progress,
    )


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


async def _wizard_route(
    choice: str,
    options: list[str],
    choose: _common.ChooseCb,
    ask_text: _elicit.AskCb,
    progress: _common.ProgressCb,
) -> str:
    """Run the flow the tester chose, or the handoff or menu for it."""
    if choice == "Generate test cases":
        return await _guided_test_cases(
            choose=choose, ask_text=ask_text, progress=progress
        )

    if choice == "Report a bug":
        return await _wizard_report_bug(choice, ask_text, progress)

    if choice == "Exploratory testing coach":
        return await _wizard_explore(choice, ask_text, progress)

    if choice in _WIZARD_HANDOFFS:
        return _wizard_handoff_markdown(choice)
    # Unrecognised selection — fall back to the full menu.
    return _wizard_menu_markdown(options)


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
            asked.value.strip(), progress=progress
        )
        return prepared + (
            "\n\n_Next: write the report exactly as the task above "
            "asks, then call `qa_submit_bug_report` with its `task_id`._"
        )
    if asked.status == _common.DECLINED:
        return "👍 Cancelled."
    return _wizard_handoff_markdown(choice)


async def _wizard_explore(
    choice: str, ask_text: _elicit.AskCb, progress: _common.ProgressCb
) -> str:
    """The wizard's exploratory flow: ask for the feature, then prepare a step."""
    asked = await _elicit._elicit_text(
        ask_text, "Which feature or area do you want to explore?"
    )
    if asked.status == _common.CHOSEN and (asked.value or "").strip():
        feature = asked.value.strip()
        # Deterministic id: running the wizard again with the same
        # feature continues the same coaching session.
        session_id = (
            "wizard-" + hashlib.sha1(feature.lower().encode("utf-8")).hexdigest()[:10]
        )
        step = await explore_bug.handle_explore_step(
            feature, session_id, progress=progress
        )
        # PREPARE half only (host-boomerang Phase 2): qa_explore_step no
        # longer returns a coaching turn, so qa_submit_explore_step --
        # not qa_wizard -- is the immediate next call.
        return step + (
            "\n\n_Next: write the coaching step the task above asks "
            "for and call `qa_submit_explore_step` with its `task_id`. "
            "After that, run `qa_wizard` again with the same feature, "
            f"or call `qa_explore_step` with session_id `{session_id}` "
            "and your `tester_response`._"
        )
    if asked.status == _common.DECLINED:
        return "👍 Cancelled."
    return _wizard_handoff_markdown(choice)


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
    await _common._audit("mcp_wizard")
    try:
        options = _wizard_options()
        top = await _elicit._elicit_choice(
            choose, "What would you like to do?", options
        )
        if top.status == _common.UNAVAILABLE:
            return _wizard_menu_markdown(options)
        if top.status == _common.DECLINED:
            # F12 (2026-08-30): a DECLINED choice is not always a tester saying
            # no. In Cursor the wizard returned this cancellation on a turn where
            # `qa_generate_test_cases` correctly rendered its markdown menu, so
            # the documented entry point for non-technical testers dead-ended on
            # the very client the session ran in. Show the menu either way: a
            # tester who really did cancel loses nothing by being shown what they
            # declined, while one whose client never rendered a dialog now has
            # something to act on.
            return (
                "👍 Nothing selected — here is the menu, or call "
                "`qa_wizard` again anytime.\n\n" + _wizard_menu_markdown(options)
            )
        choice = top.value
        return await _wizard_route(choice, options, choose, ask_text, progress)
    except Exception as exc:
        logger.exception("handle_wizard failed")
        return f"⚠️ Wizard failed: {exc}"
