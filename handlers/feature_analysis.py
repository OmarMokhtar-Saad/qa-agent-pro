"""Moved from handlers (refactor P2): feature_analysis."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import NamedTuple

from agents.feature_analysis import (
    finalize_feature_report,
    prepare_feature_analysis,
    render_report_markdown,
)
from handlers import _common, _elicit, capture, explore_bug, export, jira
from tools import telemetry
from tools.jira_fetcher import fetch_url_content
from tools.jira_mcp import bare_issue_key, issue_url_for_key, not_connected_message

logger = logging.getLogger("tools.mcp_handlers")

_FA_MODES = ("jira", "mobile", "jira_mobile")
_FA_MODE_LABELS = {
    "Jira only": "jira",
    "Mobile only": "mobile",
    "Jira + Mobile": "jira_mobile",
}


def _fa_mode_menu_markdown() -> str:
    """Markdown fallback when no mode is given and elicitation is unavailable."""
    return (
        "## Feature analysis\n\n"
        "Provide a feature description or a Jira/issue URL as `feature_or_url`, "
        "and call `qa_feature_analysis` with one of these as `mode`:\n"
        "- `jira` — analyse a feature description or Jira/issue URL\n"
        "- `mobile` — capture screens from a connected device\n"
        "- `jira_mobile` — merge the ticket with captured screens"
    )


class FeatureAnalysisReply(str):
    """The Feature Analysis reply text, plus the screens to attach as MCP
    image content.

    A ``str`` SUBCLASS on purpose. ``handle_feature_analysis`` has 17 return
    statements and 16 of them are plain text (errors, menus, cancellations);
    every caller and every existing test treats the reply as a string. This
    lets the ONE success path carry attachments without rewriting the other
    sixteen or breaking `x in reply`. ``mcp_server.qa_feature_analysis`` reads
    ``.images`` via getattr, so a plain str from any other path still works.
    """

    images: list

    def __new__(cls, text: str, images=()):
        obj = super().__new__(cls, text)
        obj.images = list(images or [])
        return obj


def _fa_vision_disclosure(screens_captured: int) -> str:
    """Disclose that captured device screens reached NO vision description.

    Ledger row `image_description.describe_images`, terminal status
    `disabled (disclosed)` (residue sub-phase R3). That token is only TRUE if
    something discloses, and until now nothing did: the reply's existing header
    is gated on `screen_descriptions`, so when the descriptions are missing it
    simply goes quiet and the tester is left assuming the screens were read.

    Fires ONLY when screens were actually captured and NOTHING came back
    (Phase 3b's narrowing rule -- never claim a loss that did not happen). Since
    the 2026-08-15 migration the mobile branch ATTACHES its screens instead of
    describing them, so the ordinary outcome is the `forwarded_screens` notice
    at the call site and THIS disclosure is the residual: screens captured,
    nothing attached and nothing described. It therefore has ONE cause and one
    wording. The second branch -- "the server-LLM switch refused the call" --
    was DELETED on 2026-08-15 together with the switch: `llm.server_llm_enabled`
    is a True constant, so that branch was unreachable, and its text named two
    settings that no longer exist.

    Never raises: a disclosure must not be able to break a prepare.
    """
    try:
        if not screens_captured:
            return ""
        why = (
            "this server produced no description for them \u2014 it makes no "
            "vision call of any kind, and the screens were not attached to "
            "the reply either \u2014 describe them yourself in the chat"
        )
        return (
            f"> \u26a0\ufe0f {screens_captured} screen(s) were captured but NOT "
            f"described: {why}. The analysis is written from the text above, "
            "not from the screens.\n\n"
        )
    except Exception:  # pragma: no cover - a disclosure never breaks a prepare
        logger.debug("_fa_vision_disclosure failed", exc_info=True)
        return ""


class _FaTicket(NamedTuple):
    """What the ticket step read: its text and whether a URL was fetched."""

    text: str = ""
    used_url: bool = False


class _FaCapture(NamedTuple):
    """What the capture step produced: the screen count and the raw screens."""

    screens_captured: int = 0
    forwarded: tuple = ()


def _fa_normalize_mode(mode: str) -> str:
    mode = (mode or "").strip().lower().replace("+", "_").replace(" ", "_")
    return "jira_mobile" if mode == "jira_and_mobile" else mode


async def _fa_resolve_mode(
    mode: str, text: str, choose: _common.ChooseCb
) -> tuple[str, str]:
    """Settle the mode: ``(mode, reply)``; a non-empty reply ends the call."""
    if mode:
        return mode, ""
    if _common._elicit_enabled():
        picked = await _elicit._elicit_choice(
            choose, "Which Feature Analysis mode?", list(_FA_MODE_LABELS)
        )
        if picked.status == _common.CHOSEN:
            mode = _FA_MODE_LABELS.get(picked.value or "", "")
        elif picked.status == _common.DECLINED:
            return "", "👍 Cancelled — no Feature Analysis mode selected."
        elif _common._unanswered(picked):
            # K1 (2026-08-10): with feature_or_url supplied -- the common case --
            # the fallthrough below silently picked mode="jira" and spent a full
            # text-only analysis on an unanswered question. Hand back the menu.
            return "", _fa_mode_menu_markdown()
    if mode:
        return mode, ""
    if text:
        return "jira", ""  # single-shot back-compat when elicitation is off
    return "", _fa_mode_menu_markdown()


def _fa_bare_key_reply(text: str, mode: str) -> str:
    """The refusal for a BARE issue key given as the feature, else "".

    2026-09-02 audit F8, round 2 (H3). The prepare handler learned that a BARE
    issue key is a ticket reference and not a feature; this entry point had
    not, and opened an analysis task whose whole "ticket text" was the eight
    characters of the key. Same input, same defect, same remedy -- fixing one
    call site of a class is not fixing the class.
    Only the modes that READ A TICKET: `mobile` analyses captured screens and
    never resolves a key, so a screen set named `LOGIN-2` is not a ticket
    reference there (round-3 review, L1).
    """
    bare_key = bare_issue_key(text) if mode in ("jira", "jira_mobile") else ""
    if not bare_key:
        return ""
    bare_url = issue_url_for_key(bare_key)
    logger.info("feature analysis: bare issue key %s read as a ticket", bare_key)
    if bare_url:
        return (
            f"⚠️ `{bare_key}` looks like a Jira issue key, not a feature "
            "description -- analysing the key alone would report on those "
            "characters rather than on the ticket. On this install that key "
            f"is <{bare_url}>. Re-send THAT url and I'll read the "
            f"ticket. If `{bare_key}` is really the feature's name, "
            "describe it in a sentence or two instead."
        )
    return (
        f"⚠️ `{bare_key}` looks like a Jira issue key, not a feature "
        "description, and this install has no `JIRA_BASE_URL` configured, so "
        "I can't turn the key into a link myself. Send the full ticket URL "
        "instead, or paste the ticket's text and I'll analyse that."
    )


def _fa_input_gate_reply(mode: str, text: str) -> str:
    """The refusal for an unusable mode or input, else ""."""
    if mode not in _FA_MODES:
        return f"⚠️ Unknown mode '{mode}'. Choose one of: {', '.join(_FA_MODES)}."
    if mode in ("jira", "jira_mobile") and not text:
        return "⚠️ Provide a feature description or a Jira/issue URL as feature_or_url."
    return _fa_bare_key_reply(text, mode)


def _fa_jira_directive_reply(url_content: dict) -> str:
    """The reply for a fetch that came back unusable, else "".

    The Atlassian-MCP fetch directive (``needs_jira_mcp``) is relayed WORD FOR
    WORD (AGENTS.md hard rule) rather than analysing a ticket we could not read.
    """
    if url_content.get("needs_jira_mcp"):
        return str(url_content.get("error") or not_connected_message())
    if url_content.get("error"):
        # 2026-08-31, the same defect as the prepare path: mode="jira"
        # means the TICKET is the input, so an unreadable one is a
        # refusal. This used to fall through and analyse an empty string.
        return str(url_content.get("error"))
    return ""


async def _fa_fetch_ticket(
    mode: str, text: str, jira_content_json: str, progress: _common.ProgressCb
) -> tuple[_FaTicket, str]:
    """Read the ticket behind a URL: ``(ticket, reply)``; a reply ends the call."""
    if mode not in ("jira", "jira_mobile") or not _common._is_url(text):
        return _FaTicket(), ""
    await _common._emit(progress, "\U0001f517 Fetching the ticket…")
    hint = jira._jira_config_hint(text)
    if hint:
        return _FaTicket(), hint
    if jira_content_json:
        url_content = await fetch_url_content(text, jira_content=jira_content_json)
    else:
        url_content = await fetch_url_content(text)
    reply = _fa_jira_directive_reply(url_content)
    if reply:
        return _FaTicket(), reply
    return _FaTicket(url_content.get("content") or "", True), ""


async def _fa_pick_device_id(
    device_id: str, choose: _common.ChooseCb
) -> tuple[str, str]:
    """The device id to capture from: ``(device_id, reply)``."""
    device_id = (device_id or "").strip()
    if device_id or not _common._elicit_enabled():
        return device_id, ""
    picked = await _elicit._elicit_device(choose)
    if picked.status == _common.CHOSEN:
        return picked.value or "", ""
    if picked.status == _common.DECLINED:
        return "", "👍 Cancelled — no device selected."
    return "", await capture._device_menu_markdown(tool="qa_feature_analysis")


async def _fa_capture_stage(
    mode: str, device_id: str, choose: _common.ChooseCb, progress: _common.ProgressCb
) -> tuple[_FaCapture, str]:
    """Capture the device screens on the mobile modes: ``(capture, reply)``."""
    if mode not in ("mobile", "jira_mobile"):
        return _FaCapture(), ""
    if not _common._mobile_capture():
        if mode == "mobile":
            return _FaCapture(), "ℹ️ Mobile capture is disabled in this build."
        return _FaCapture(), ""  # jira_mobile continues from the ticket alone
    device_id, reply = await _fa_pick_device_id(device_id, choose)
    if reply:
        return _FaCapture(), reply
    device, why_not = await _common._resolve_device_with_reason(device_id)
    if device is None:
        return _FaCapture(), (
            f"⚠️ Device `{device_id}` not found. Run qa_list_devices to "
            "see connected devices, then pass a listed id." + why_not
        )
    screens, capture_error = await capture._fa_capture_screens(
        device, choose=choose, progress=progress
    )
    if capture_error and not screens:
        return _FaCapture(), f"⚠️ Couldn't capture a screenshot: {capture_error}"
    if screens:
        # NO server vision call: the raw screens ride to the tester's own
        # multimodal model as MCP image content, exactly as qa_capture_screens
        # already returns them and as IMAGE_JOB does on the generation path.
        await _common._emit(
            progress,
            f"🖼️ Attaching {len(screens)} captured screen(s) for your model…",
        )
    return _FaCapture(len(screens), tuple(screens)), ""


def _fa_reply_header(mode: str, capture: _FaCapture) -> str:
    """The mode line and the honest note on what happened to the screens."""
    header = ""
    if mode != "jira":
        header = f"_Mode: {mode} · screens captured: {capture.screens_captured}_\n\n"
    if capture.forwarded:
        # The screens are ATTACHED to this reply, so the old two-branch
        # vision disclosure would now be a lie in both directions: nothing
        # was described server-side, and nothing was lost either.
        header += (
            "> ℹ️  "
            f"{len(capture.forwarded)} captured screen(s) are attached to "
            "this message as images. This server made NO vision call — "
            "read them yourself.\n\n"
        )
    elif capture.screens_captured:
        # Screens were captured and NOTHING described them (the server makes
        # no vision call): the tester must not be left believing they were read.
        header += _fa_vision_disclosure(capture.screens_captured)
    return header


async def _fa_audit_prepare(
    mode: str, ticket: _FaTicket, task_id: str, screens: int
) -> None:
    """Audit the opened Feature Analysis task."""
    await _common._audit(
        "mcp_feature_analysis_prepare",
        entity_id=task_id,
        detail={
            "mode": mode,
            "source": "url" if ticket.used_url else "text",
            "screens": screens,
        },
    )


async def _fa_prepare_reply(
    mode: str,
    text: str,
    ticket: _FaTicket,
    capture: _FaCapture,
    progress: _common.ProgressCb,
) -> str | FeatureAnalysisReply:
    """Open the analysis task, audit it and render the reply for the host."""
    screens_captured = capture.screens_captured
    source = "jira" if ticket.used_url else "mobile" if screens_captured else "text"
    await _common._emit(
        progress, "📋 Preparing the Feature Analysis task for your chat model…"
    )
    opened = await prepare_feature_analysis(
        text or "Feature captured from mobile device screens.",
        ticket.text,
        "",
        mode=mode,
        screens=screens_captured,
        source=source,
    )
    if opened.get("error"):
        return f"⚠️ Feature Analysis preparation failed: {opened['error']}"
    opened_content = opened.get("content") or {}
    task_id = str(opened_content.get("task_id") or "")
    telemetry.add_tool_properties(source=source)
    await _fa_audit_prepare(mode, ticket, task_id, screens_captured)
    envelope = opened_content.get("envelope") or {}
    # F16 (2026-08-30): `shape_host_task`'s response_schema is opt-IN; without
    # it the host guesses field names and a wrong guess fails INVISIBLY
    # (finalize_feature_report drops unknown keys by design).
    return FeatureAnalysisReply(
        _fa_reply_header(mode, capture)
        + explore_bug.shape_host_task(
            "Feature Analysis — your turn",
            task_id,
            envelope,
            "qa_submit_feature_analysis",
            "field `report_json`",
            response_schema=envelope.get("response_schema"),
        ),
        list(capture.forwarded),
    )


async def handle_feature_analysis(
    feature_or_url: str = "",
    mode: str = "",
    device_id: str = "",
    *,
    choose: _common.ChooseCb = None,
    progress: _common.ProgressCb = None,
    jira_content_json: str = "",
) -> str:
    """PREPARE half of the chat-only standalone Feature Analysis (Phase 4).

    The server makes NO model call for the ANALYSIS. It still runs the mode menu
    (jira / mobile / jira_mobile), the device pick and capture-another screenshot
    loop on the mobile modes, and then ATTACHES the captured screens to the
    reply as MCP image content -- since 2026-08-15 there is no server-side
    vision call on this path at all; the tester's own multimodal model reads
    the screens -- then hands that model a task envelope. The written report comes
    back through `qa_submit_feature_analysis`, which coerces the untrusted
    payload, renders it and records the delivered-artifact audit row. A plain
    call with only feature_or_url (no mode, no elicitation) still covers the
    original single-shot text/Jira analysis, now in two steps. Never raises."""
    # Defence in depth (2026-08-03): mcp_server.py does not REGISTER this tool
    # in the test-cases-only edition, so this handler is unreachable over MCP
    # there -- but that edition is credential-free by design and the mobile
    # modes below call this server's own vision, so the handler refuses on its
    # own too rather than depending on one registration gate. Same shape as
    # handle_bug_report / handle_explore_step.
    if _common._test_cases_only():
        return export._TEST_CASES_ONLY_NOTICE
    if not _common._feature_analysis_enabled():
        return "ℹ️ Feature Analysis is disabled in this build."
    text = (feature_or_url or "").strip()
    mode, reply = await _fa_resolve_mode(_fa_normalize_mode(mode), text, choose)
    reply = reply or _fa_input_gate_reply(mode, text)
    if reply:
        return reply

    try:
        ticket, reply = await _fa_fetch_ticket(mode, text, jira_content_json, progress)
        if reply:
            return reply
        capture, reply = await _fa_capture_stage(mode, device_id, choose, progress)
        if reply:
            return reply
        return await _fa_prepare_reply(mode, text, ticket, capture, progress)
    except Exception as exc:
        logger.exception("handle_feature_analysis failed")
        _common._capture_error(exc, "qa_feature_analysis")
        return f"⚠️ Feature analysis failed: {exc}"


# First submission plus ONE structured resubmit round. Re-running the PREPARE
# instead is not equivalent on the mobile modes -- it would re-drive the device
# capture-another loop -- so the prompt inputs ride on the task record and a
# round 2 rebuilds the prompt from them against a NEW task id (a task id is
# one-shot). A still-unusable round 2 renders the blank report WITH a warning:
# never discard the tester's turn, never pretend it worked.
_MAX_FEATURE_ANALYSIS_ROUNDS = 2


async def _write_feature_analysis_file(task_id: str, markdown: str) -> str:
    """Write the FULL Feature Analysis report beside the other exports.

    F1 (2026-08-30). The submit reply renders the report COMPACT -- four of its
    sixteen sections -- and ``render_report_markdown`` justifies that mode on
    the grounds that "the full analysis is delivered as a downloadable file".
    No caller ever wrote one. Twelve sections the rubric spends seven numbered
    steps asking the host for -- every functional requirement, every acceptance
    criterion, the preconditions, roles, dependencies, validation rules, error
    handling, edge cases, the assumptions, the UI analysis, the user flow, and
    the conflicts the rubric says to "never silently drop" -- were submitted,
    validated and thrown away, with nothing in the reply to say so.

    Returns a markdown line naming the path, or "" when there is no configured
    export dir or the write fails: a missing file must never cost the tester the
    report body. Never raises.
    """
    try:
        export_dir = export._resolved_export_dir()
        if not export_dir:
            return ""
        dest = Path(export_dir).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        safe_id = (
            "".join(ch for ch in str(task_id or "") if ch.isalnum() or ch in "-_")[:32]
            or "report"
        )
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = dest / f"qa_feature_analysis_{safe_id}_{stamp}.md"
        path.write_text(markdown, encoding="utf-8")
        # LOGGED, not audited: the mcp_feature_analysis row is this submission's
        # audit event and tests pin it as the last one, so a second row here
        # would make the artifact write look like the submission.
        logger.info("feature analysis report written to %s", path)
        return (
            f"**Full report (all sections):** `{path}`\n\n"
            "The summary below is the short view; the file has the requirement "
            "analysis, assumptions, UI analysis, user flow and conflicts.\n\n"
        )
    except Exception:
        logger.warning("could not write the Feature Analysis file", exc_info=True)
        return ""


async def _fa_resubmit_reply(task_id: str, meta: dict, usable: bool) -> str | None:
    """The round-2 resubmit task for an unusable report, or None to render as is."""
    round_no = int(meta.get("round") or 1)
    mode = str(meta.get("mode") or "")
    screens_n = int(meta.get("screens") or 0)
    if not usable and round_no < _MAX_FEATURE_ANALYSIS_ROUNDS:
        reopened = await prepare_feature_analysis(
            str(meta.get("feature_text") or ""),
            str(meta.get("jira_text") or ""),
            str(meta.get("screen_descriptions") or ""),
            mode=mode,
            screens=screens_n,
            source=str(meta.get("source") or ""),
            round_no=round_no + 1,
        )
        reopened_content = reopened.get("content") or {}
        if not reopened.get("error") and reopened_content.get("task_id"):
            return (
                "> ⚠️ That submission carried no usable Feature Analysis "
                "object. Re-emit it as a SINGLE JSON object matching "
                "`response_schema` and submit it against the NEW task id "
                "below.\n\n"
                + explore_bug.shape_host_task(
                    "Feature Analysis — resubmit (round 2 of 2)",
                    str(reopened_content.get("task_id") or ""),
                    reopened_content.get("envelope") or {},
                    "qa_submit_feature_analysis",
                    "field `report_json`",
                    response_schema=(reopened_content.get("envelope") or {}).get(
                        "response_schema"
                    ),
                )
            )
        logger.warning(
            "could not open a resubmit round for feature-analysis task %s — "
            "rendering what the host sent",
            task_id,
        )
    return None


async def _fa_report_reply(
    task_id: str, meta: dict, report: object, usable: bool
) -> str:
    """Audit the submission and render the report, with a note on the saved file."""
    round_no = int(meta.get("round") or 1)
    mode = str(meta.get("mode") or "")
    screens_n = int(meta.get("screens") or 0)
    await _common._audit(
        "mcp_feature_analysis",
        entity_id=task_id,
        detail={
            "mode": mode,
            "source": str(meta.get("source") or ""),
            "screens": screens_n,
            "round": round_no,
            "usable": usable,
        },
    )
    header = "## Feature Analysis\n\n"
    if mode and mode != "jira":
        header += f"_Mode: {mode} · screens captured: {screens_n}_\n\n"
    if not usable:
        header += (
            "> ⚠️ The submitted JSON carried no usable report, so every "
            "section below is empty. Call `qa_feature_analysis` again to "
            "retry.\n\n"
        )
    file_note = await _write_feature_analysis_file(
        task_id, header + render_report_markdown(report)
    )
    return header + file_note + render_report_markdown(report, compact=True)


async def handle_submit_feature_analysis(
    task_id: str, report_json: str, *, progress: _common.ProgressCb = None
) -> str:
    """SUBMIT half of the chat-only Feature Analysis report. Never raises.

    The host wrote the report; this server coerces the UNTRUSTED payload into a
    ``FeatureAnalysisReport`` (unknown keys dropped, no fabrication), renders it
    and records the audit row -- the same side effects the server always owned.
    """
    # Defence in depth -- see handle_feature_analysis above (2026-08-03).
    if _common._test_cases_only():
        return export._TEST_CASES_ONLY_NOTICE
    if not _common._feature_analysis_enabled():
        return "ℹ️ Feature Analysis is disabled in this build."
    task_id = (task_id or "").strip()
    if not task_id:
        return "⚠️ Pass the `task_id` from `qa_feature_analysis`."
    if not (report_json or "").strip():
        return "⚠️ Send the Feature Analysis JSON you wrote as `report_json`."
    try:
        from tools import host_llm as _host_llm

        await _common._emit(progress, "📋 Checking the Feature Analysis report…")
        closed = await _host_llm.close_task(
            task_id, report_json, expect_kind="feature_analysis"
        )
        if closed.get("error"):
            return (
                f"⚠️ {closed['error']}. Start again with `qa_feature_analysis` — "
                "a task id is one-shot and expires with the prep TTL."
            )
        content = closed.get("content") or {}
        meta = content.get("meta") or {}
        report, usable = finalize_feature_report(content.get("payload"))
        resubmit = await _fa_resubmit_reply(task_id, meta, usable)
        if resubmit is not None:
            return resubmit
        return await _fa_report_reply(task_id, meta, report, usable)
    except Exception as exc:
        logger.exception("handle_submit_feature_analysis failed")
        _common._capture_error(exc, "qa_submit_feature_analysis")
        return f"⚠️ Feature Analysis submission failed: {exc}"
