"""MCP handler layer — task-shaped, transport-agnostic wrappers over the QA
agents and tools (gated by QA_MCP_ENABLED at the server layer).

This module holds the BUSINESS LOGIC behind every MCP tool exposed by
``mcp_server.py``. Each handler calls the existing agents / tools, writes an
audit event (the Chainlit auth + rate-limit layer is bypassed on the MCP
transport, so every tool call must leave a trail), and shapes the result into
CONCISE markdown — never a raw JSON dump.

It imports NOTHING from ``fastmcp`` so it stays fully unit-testable with the
mocked test suite. ``mcp_server.py`` adapts the FastMCP request ``Context`` into
a plain async ``progress`` callback ``(message: str) -> Awaitable[None]`` that
these handlers invoke to reset the client's tool-call timeout during long
generation / device runs; a ``None`` callback is a no-op.

House rules honoured here:
  * Never raises to the caller — every handler returns a friendly markdown
    string on failure (mirrors the agents' never-raise contract).
  * All LLM access stays inside the agents/tools this layer delegates to
    (llm.ask / ask_json / ask_vision), so all three backends keep working.
  * External text (Jira/URL content) is wrapped by the agents it calls before
    reaching the LLM — this layer never assembles a raw prompt itself.
  * No bare print — logging only. Secrets come from settings (.env) only.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from agents.feature_analysis import analyze_feature, render_report_markdown
from agents.test_scenario_agent import generate_test_scenarios
from config.settings import settings
from tools.audit_log import record_event
from tools.csv_exporter import generate_test_case_csv
from tools.device_manager import (
    capture_screenshot,
    list_devices,
    list_installed_apps,
)
from tools.gherkin_exporter import generate_feature_file
from tools.image_description import describe_images
from tools.jira_fetcher import fetch_url_content
from tools.playwright_exporter import generate_playwright_script
from tools.rag_store import query_corpus
from tools.suite_store import list_recent_suites, load_suite, save_suite
from tools.swagger_fetcher import fetch_openapi_spec, looks_like_openapi_url
from tools.testrail_exporter import generate_testrail_csv
from tools.ui_extractor import extract_ui_elements
from tools.xlsx_generator import generate_test_case_xlsx

# The distribution build ships ONLY the test-case pipeline (see QA_DIST_MODE):
# bug-report / exploratory-coach / Maestro modules are absent there, so their
# imports are guarded. mcp_server.py skips registering the excluded tools when
# _test_cases_only() is true; the handler gates below are defense in depth.
try:
    from agents.bug_report_agent import generate_bug_report
    from agents.exploratory_coach_agent import coach_next_step
    from tools.coach_memory import (
        create_session_memory,
        strip_meta,
        update_coverage,
    )
    from tools.maestro_explorer import explore as maestro_explore
    from tools.maestro_exporter import flow_dir_for_suite, generate_maestro_flows
    from tools.maestro_healer import heal_and_rerun
    from tools.maestro_runner import run_flows

    _FULL_EDITION = True
except ImportError:  # pragma: no cover — exercised only in distribution builds
    generate_bug_report = None
    coach_next_step = None
    create_session_memory = None
    strip_meta = None
    update_coverage = None
    maestro_explore = None
    flow_dir_for_suite = None
    generate_maestro_flows = None
    heal_and_rerun = None
    run_flows = None

    _FULL_EDITION = False

logger = logging.getLogger(__name__)

# Baked by scripts/build_dist.py in the public distribution ("owner/repo").
# Empty in the private checkout, which disables the on-demand update path.
_DIST_UPDATE_REPO = "OmarMokhtar-Saad/qa-agent-pro"


def _schedule_reload() -> None:
    """Exit the server process shortly after the current response flushes.

    Distribution installs only, right after an on-demand update: the
    supervising launcher (start.sh) respawns the server on the NEW code and
    replays the MCP handshake, so the editor session never notices."""

    def _later() -> None:
        time.sleep(2)
        os._exit(86)

    threading.Thread(target=_later, daemon=True).start()


def _test_cases_only() -> bool:
    """True when only the test-case tools should be exposed: the distribution
    build (optional modules absent) or QA_DIST_MODE=true."""
    return settings.qa_dist_mode or not _FULL_EDITION


_TEST_CASES_ONLY_NOTICE = (
    "⚠️ This edition generates test cases only — this tool is not available. "
    "Use qa_generate_test_cases, qa_feature_analysis or qa_export_suite."
)

ProgressCb = Optional[Callable[[str], Awaitable[None]]]

# --- Guided-wizard choice callback (MCP elicitation bridge) ----------------- #
# mcp_server.py adapts FastMCP's ctx.elicit into this async callback, mirroring
# ProgressCb. It returns a ChoiceResult so these handlers stay importable and
# testable WITHOUT fastmcp and can react to accept / decline / (elicitation-)
# unavailable uniformly. A None callback (flag off / no client) is UNAVAILABLE.
CHOSEN = "chosen"
DECLINED = "declined"
UNAVAILABLE = "unavailable"


@dataclass
class ChoiceResult:
    """Outcome of one elicitation round. ``status`` is CHOSEN/DECLINED/UNAVAILABLE;
    ``value`` carries the selected option string when status is CHOSEN."""

    status: str
    value: str | None = None


ChooseCb = Optional[Callable[[str, list], Awaitable["ChoiceResult"]]]

# Hard cap on elicitation rounds per tool call so a wizard can never loop forever.
_MAX_ELICIT_ROUNDS = 5

# Actor recorded on every MCP audit event. The MCP transport carries no logged-in
# tester identity (unlike the Chainlit UI), so all its events share this actor.
_ACTOR = "mcp"

# In-process exploratory-session store keyed by the caller-supplied session_id.
# The MCP server is a long-running stdio process, so this dict persists across
# tool calls within one session — the coach_memory module itself is stateless.
_SESSIONS: dict[str, dict] = {}

# Each entry is a thin lambda (not a direct function reference) so the
# exporter is looked up from this module's global namespace at *call* time.
# A direct reference (`"csv": generate_test_case_csv`) would bind the
# original function object at import time, silently defeating
# `monkeypatch.setattr(mcp_handlers, "generate_test_case_csv", ...)` in tests
# (and any future runtime patching) because the dict would keep pointing at
# the pre-patch function.
_EXPORTERS: dict[str, Callable] = {
    "csv": lambda s: generate_test_case_csv(s),
    "xlsx": lambda s: generate_test_case_xlsx(s),
    "gherkin": lambda s: generate_feature_file(s),
    "playwright": lambda s: generate_playwright_script(s),
    "testrail": lambda s: generate_testrail_csv(s),
}

_MOBILE_MODES = ("export", "run", "heal", "explore")

_SUMMARY_CAP = 4000  # cap the embedded generation summary in the shaped result


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


async def _emit(progress: ProgressCb, message: str) -> None:
    """Best-effort progress ping — a failing/absent callback never breaks a tool."""
    if progress is None:
        return
    try:
        await progress(message)
    except Exception:
        logger.debug("mcp progress callback failed for %r", message, exc_info=True)


async def _audit(
    event_type: str, entity_id: str | None = None, detail: dict | None = None
) -> None:
    """Record one MCP audit event, swallowing any failure (never-raise)."""
    try:
        await record_event(
            event_type, actor=_ACTOR, entity_id=entity_id, detail=detail or {}
        )
    except Exception:
        logger.debug("mcp audit record_event failed", exc_info=True)


def _is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


async def _resolve_device(device_id: str) -> dict | None:
    """Look up a full device dict (platform/kind) by id from live discovery."""
    if not device_id:
        return None
    result = await list_devices()
    for dev in result.get("content") or []:
        if dev.get("id") == device_id:
            return dev
    return None


def _resolve_flow_path(suite_id: str) -> str:
    """The on-disk Maestro flow directory for run/heal.

    Mode "export" writes flows to the PER-SUITE dir flow_dir_for_suite(suite_id)
    (maestro_flows/<suite_id>), mirroring app.py. run/heal must read from that
    same dir when a suite_id is supplied; otherwise they fall back to the parent
    settings.qa_maestro_flow_dir (a hand-populated flow dir).
    """
    suite_id = (suite_id or "").strip()
    return flow_dir_for_suite(suite_id) if suite_id else settings.qa_maestro_flow_dir


# --------------------------------------------------------------------------- #
# Elicitation helpers (never-raise; degrade to UNAVAILABLE -> markdown menu)   #
# --------------------------------------------------------------------------- #


async def _elicit_choice(choose: ChooseCb, message: str, options: list) -> ChoiceResult:
    """Run one elicitation round, degrading to UNAVAILABLE on a missing callback
    or any transport error (e.g. a client without elicitation support)."""
    if choose is None:
        return ChoiceResult(UNAVAILABLE)
    try:
        result = await choose(message, list(options))
    except Exception:
        logger.debug("mcp elicit_choice failed for %r", message, exc_info=True)
        return ChoiceResult(UNAVAILABLE)
    if isinstance(result, ChoiceResult):
        return result
    return ChoiceResult(UNAVAILABLE)


# The free-text sibling of ChooseCb: ``ask_text(message) -> ChoiceResult`` where
# CHOSEN carries the typed text. Provided by the transport adapter
# (mcp_server._make_asker); None when elicitation is off/unsupported.
AskCb = Optional[Callable[[str], Awaitable["ChoiceResult"]]]


async def _elicit_text(ask_text: AskCb, message: str) -> ChoiceResult:
    """One free-text elicitation round; UNAVAILABLE without a callback or on
    any transport error (mirrors _elicit_choice)."""
    if ask_text is None:
        return ChoiceResult(UNAVAILABLE)
    try:
        result = await ask_text(message)
    except Exception:
        logger.debug("mcp elicit_text failed for %r", message, exc_info=True)
        return ChoiceResult(UNAVAILABLE)
    if isinstance(result, ChoiceResult):
        return result
    return ChoiceResult(UNAVAILABLE)


async def _elicit_suite(choose: ChooseCb) -> ChoiceResult:
    """Offer the most recent stored suites as a choice (non-technical parity
    with Chainlit's recent-suites list). CHOSEN carries the suite_id."""
    result = await list_recent_suites(limit=5)
    rows = result.get("content") or []
    if not rows:
        return ChoiceResult(UNAVAILABLE)
    labels: list = []
    by_label: dict = {}
    for row in rows:
        feature = " ".join((row.get("feature_text") or "untitled").split())[:60]
        label = (
            f"{feature} — {row.get('case_count', 0)} cases "
            f"({str(row.get('suite_id'))[:8]}…)"
        )
        labels.append(label)
        by_label[label] = row.get("suite_id")
    picked = await _elicit_choice(choose, "Which suite?", labels)
    if picked.status == CHOSEN:
        return ChoiceResult(CHOSEN, by_label.get(picked.value or ""))
    return picked


async def _recent_suites_markdown(tool: str) -> str:
    """Markdown fallback: list recent stored suites plus a re-call instruction."""
    result = await list_recent_suites(limit=5)
    rows = result.get("content") or []
    if not rows:
        return (
            "⚠️ Provide the suite_id returned by qa_generate_test_cases "
            "(no stored suites yet — generate one first)."
        )
    lines = ["⚠️ Provide a `suite_id`. Recent suites:", ""]
    for row in rows:
        feature = " ".join((row.get("feature_text") or "untitled").split())[:60]
        lines.append(
            f"- `{row.get('suite_id')}` — {feature} ({row.get('case_count', 0)} cases)"
        )
    lines.append("")
    lines.append(f"Re-call `{tool}` with the chosen `suite_id`.")
    return "\n".join(lines)


def _mobile_mode_options() -> list:
    """Maestro modes offered by the wizard/elicitation, filtered by their gates.
    export/run are always available under QA_MAESTRO_ENABLED; heal/explore need
    their own flags (mirrors app.py's _mt_mode_actions)."""
    modes = ["export", "run"]
    if settings.qa_maestro_heal_enabled:
        modes.append("heal")
    if settings.qa_maestro_explore_enabled:
        modes.append("explore")
    return modes


def _device_options(devices: list):
    """Build (labels, label->id) for a device elicitation. The label leads with
    the device name and carries the id so distinct devices sharing a name stay
    unambiguous when the selected label is mapped back to an id."""
    labels: list = []
    by_label: dict = {}
    for dev in devices:
        did = dev.get("id")
        label = f"{dev.get('name') or did} ({did})"
        labels.append(label)
        by_label[label] = did
    return labels, by_label


async def _elicit_device(choose: ChooseCb) -> ChoiceResult:
    """Scan live devices and elicit one by name. Returns CHOSEN with the device
    id, or UNAVAILABLE (no callback / no devices / transport error)."""
    result = await list_devices()
    devices = result.get("content") or []
    if not devices:
        return ChoiceResult(UNAVAILABLE)
    labels, by_label = _device_options(devices)
    picked = await _elicit_choice(choose, "Which device?", labels)
    if picked.status == CHOSEN:
        return ChoiceResult(CHOSEN, by_label.get(picked.value or ""))
    return picked


async def _device_menu_markdown(tool: str = "qa_run_mobile_suite") -> str:
    """Markdown fallback for the device picker: the live device list plus a
    re-call instruction for *tool*."""
    result = await list_devices()
    devices = result.get("content") or []
    return shape_devices(devices) + (
        f"\n\nRe-call `{tool}` with the chosen `device_id`."
    )


async def _elicit_mobile_mode(choose: ChooseCb) -> ChoiceResult:
    """Elicit a Maestro mode from the gated option list (shared by the wizard and
    handle_run_mobile_suite)."""
    return await _elicit_choice(
        choose, "Which mobile testing mode?", _mobile_mode_options()
    )


def _mobile_mode_menu_markdown() -> str:
    modes = _mobile_mode_options()
    return (
        "## Mobile testing\n\n"
        "Call `qa_run_mobile_suite` with one of these modes as `mode`:\n"
        + "\n".join(f"- `{m}`" for m in modes)
    )


def _format_menu_markdown() -> str:
    return (
        "## Export format\n\n"
        "Call `qa_export_suite` with one of these as `format`:\n"
        + "\n".join(f"- `{f}`" for f in sorted(_EXPORTERS))
    )


# --------------------------------------------------------------------------- #
# Markdown shapers (pure, never-raise)
# --------------------------------------------------------------------------- #


def shape_generation_result(summary: str, suite, suite_id: str, status: str) -> str:
    cases = len(getattr(suite, "test_cases", []) or []) if suite is not None else 0
    icon = {"ok": "✅", "partial": "⚠️", "fallback": "⚠️", "error": "❌"}.get(status, "ℹ️")
    lines = [f"## {icon} Test cases generated", ""]
    if suite_id:
        lines.append(f"**Suite ID:** `{suite_id}` — pass this to `qa_export_suite`.")
    lines.append(f"**Cases:** {cases}")
    lines.append(f"**Status:** {status}")
    body = (summary or "").strip()
    if body:
        if len(body) > _SUMMARY_CAP:
            body = (
                body[:_SUMMARY_CAP].rstrip()
                + "\n\n…(truncated — export the suite for the full set)"
            )
        lines += ["", body]
    return "\n".join(lines)


def shape_export_result(suite_id: str, fmt: str, path: str, count: int) -> str:
    return (
        f"## ✅ Exported suite `{suite_id}` to {fmt}\n\n"
        f"**Cases:** {count}\n"
        f"**File:** `{path}`"
    )


def shape_corpus_hits(query: str, hits: list) -> str:
    if not hits:
        return f"No corpus matches for **{query}**."
    lines = [f"## Corpus matches for “{query}” ({len(hits)})", ""]
    for hit in hits[:10]:
        score = hit.get("score", 0.0)
        snippet = (hit.get("content") or "").replace("\n", " ").strip()[:160]
        meta = hit.get("metadata") or {}
        label = meta.get("feature") or meta.get("description") or ""
        prefix = f"{label}: " if label else ""
        lines.append(f"- (score={score:.2f}) {prefix}{snippet}")
    return "\n".join(lines)


def shape_devices(devices: list) -> str:
    if not devices:
        return (
            "No devices detected. Connect an Android device/emulator or boot an "
            "iOS simulator, then retry `qa_list_devices`."
        )
    lines = [f"## Devices ({len(devices)})", ""]
    for dev in devices:
        lines.append(
            f"- `{dev.get('id')}` — {dev.get('name')} "
            f"({dev.get('platform')}/{dev.get('kind')})"
        )
    return "\n".join(lines)


def shape_explore_step(session_id: str, sess: dict, step: str) -> str:
    mem = sess.get("memory") or {}
    covered = mem.get("covered_areas") or []
    turns = mem.get("turn_count", 0)
    return "\n".join(
        [
            f"## Exploratory coaching — session `{session_id}`",
            "",
            step.strip() or "(no step produced)",
            "",
            f"_Turn {turns} · covered areas: {', '.join(covered) if covered else 'none yet'}_",
            "",
            "Reply with what you found via `qa_explore_step` (same session_id) to continue.",
        ]
    )


def shape_mobile_export(suite_id: str, path: str, count: int) -> str:
    return (
        f"## ✅ Maestro flows exported for suite `{suite_id}`\n\n"
        f"**Flows:** {count}\n"
        f"**Directory:** `{path}`\n\n"
        'Run them with `qa_run_mobile_suite(mode="run", device_id=…, suite_id='
        f'"{suite_id}")`.'
    )


def shape_mobile_run(device_id: str, payload: dict) -> str:
    if payload.get("dry_run"):
        cmd = payload.get("command") or ""
        extra = f"\n\n```\n{cmd}\n```" if cmd else ""
        return (
            f"## Maestro run (dry-run) on `{device_id}`\n\n"
            "Dry-run is ON (QA_MAESTRO_DRY_RUN) — nothing was executed on the "
            "device. Set QA_MAESTRO_DRY_RUN=false to run for real." + extra
        )
    return (
        f"## Maestro run on `{device_id}`\n\n"
        f"**Passed:** {payload.get('passed', 0)}  ·  "
        f"**Failed:** {payload.get('failed', 0)}  ·  "
        f"**Total:** {payload.get('total', 0)}"
    )


def shape_mobile_heal(device_id: str, payload: dict) -> str:
    if payload.get("reason") == "disabled":
        return "ℹ️ Maestro heal is disabled (set QA_MAESTRO_HEAL_ENABLED=true)."
    return (
        f"## Maestro heal on `{device_id}`\n\n"
        f"**Classification:** {payload.get('classification', 'unknown')}\n"
        f"**Healed:** {payload.get('healed', False)}\n"
        f"**Attempts:** {payload.get('attempts', 0)}"
    )


def shape_mobile_explore(device_id: str, payload: dict) -> str:
    if payload.get("reason") == "disabled":
        return "ℹ️ Maestro exploratory mode is disabled (set QA_MAESTRO_EXPLORE_ENABLED=true)."
    steps = payload.get("steps") or []
    summary = (payload.get("summary") or "").strip()
    head = (
        f"## Maestro exploratory run on `{device_id}`\n\n"
        f"**Steps:** {len(steps)}  ·  "
        f"**Stop reason:** {payload.get('stop_reason', 'n/a')}  ·  "
        f"**Possible bug:** {payload.get('possible_bug', False)}"
    )
    return f"{head}\n\n{summary}" if summary else head


# --------------------------------------------------------------------------- #
# Handlers (async, never-raise, return concise markdown)
# --------------------------------------------------------------------------- #


async def handle_generate_test_cases(
    feature_or_url: str,
    *,
    attached_images: list | None = None,
    force_feature_report: bool = False,
    choose: ChooseCb = None,
    ask_text: AskCb = None,
    progress: ProgressCb = None,
) -> str:
    text = (feature_or_url or "").strip()
    if not text:
        # No source given — run the guided picker (dialogs where the client
        # supports elicitation, markdown menu otherwise) instead of erroring.
        return await _guided_test_cases(
            choose=choose, ask_text=ask_text, progress=progress
        )
    try:
        url_content = None
        ui_content = None
        openapi_text = None
        if _is_url(text):
            # Swagger/OpenAPI link (QA_SWAGGER_ENABLED): condense the spec into
            # an endpoint summary instead of the generic page/UI path.
            if settings.qa_swagger_enabled and looks_like_openapi_url(text):
                await _emit(progress, "🔗 Fetching the OpenAPI spec…")
                spec_result = await fetch_openapi_spec(text)
                if not spec_result.get("error"):
                    openapi_text = spec_result.get("summary") or None
            if openapi_text is None:
                await _emit(progress, "🔗 Fetching the ticket / page…")
                url_content = await fetch_url_content(text)
                try:
                    ui_content = await extract_ui_elements(text, prefetched=url_content)
                except Exception:
                    logger.debug("mcp: UI extraction failed — continuing", exc_info=True)
                    ui_content = None

        captured: dict = {}

        def _on_ready(suite) -> None:
            captured["suite"] = suite

        async def _on_status(msg: str) -> None:
            await _emit(progress, msg)

        async def _on_progress(count: int) -> None:
            await _emit(progress, f"🧪 {count} test cases generated so far…")

        summary, _x, _c, _t, status = await generate_test_scenarios(
            feature_text=text,
            url_content=url_content,
            ui_content=ui_content,
            openapi_text=openapi_text,
            attached_images=attached_images,
            force_feature_report=force_feature_report,
            defer_files=True,
            on_suite_ready=_on_ready,
            on_status=_on_status,
            on_progress=_on_progress,
        )

        suite = captured.get("suite")
        suite_id = ""
        if suite is not None and getattr(suite, "test_cases", None):
            await _emit(progress, "💾 Saving the suite…")
            saved = await save_suite(
                suite,
                feature_text=text,
                source_url=(text if url_content else None),
            )
            suite_id = (saved.get("content") or {}).get(
                "suite_id", ""
            ) or suite.suite_id
        await _audit(
            "mcp_generate_test_cases",
            entity_id=suite_id or None,
            detail={
                "status": status,
                "cases": len(getattr(suite, "test_cases", []) or []),
            },
        )
        return shape_generation_result(summary, suite, suite_id, status)
    except Exception as exc:
        logger.exception("handle_generate_test_cases failed")
        return f"⚠️ Test-case generation failed: {exc}"


async def handle_export_suite(
    suite_id: str,
    fmt: str,
    *,
    choose: ChooseCb = None,
    progress: ProgressCb = None,
) -> str:
    fmt = (fmt or "").strip().lower()
    if not fmt and settings.qa_mcp_elicit_enabled:
        picked = await _elicit_choice(
            choose, "Which export format?", list(sorted(_EXPORTERS))
        )
        if picked.status == CHOSEN:
            fmt = (picked.value or "").strip().lower()
        elif picked.status == DECLINED:
            return "👍 Cancelled — no export format selected."
        else:
            return _format_menu_markdown()
    if fmt not in _EXPORTERS:
        return (
            f"⚠️ Unknown format '{fmt}'. Choose one of: {', '.join(sorted(_EXPORTERS))}."
        )
    suite_id = (suite_id or "").strip()
    if not suite_id and settings.qa_mcp_elicit_enabled:
        picked = await _elicit_suite(choose)
        if picked.status == CHOSEN:
            suite_id = (picked.value or "").strip()
        elif picked.status == DECLINED:
            return "👍 Cancelled — no suite selected."
        else:
            return await _recent_suites_markdown("qa_export_suite")
    if not suite_id:
        return await _recent_suites_markdown("qa_export_suite")
    try:
        loaded = await load_suite(suite_id)
        if loaded.get("error"):
            return f"⚠️ Could not load suite `{suite_id}`: {loaded['error']}"
        suite = loaded.get("content")
        if suite is None:
            return f"⚠️ No stored suite with id `{suite_id}`. Generate one first."
        await _emit(progress, f"📦 Exporting suite to {fmt}…")
        try:
            path = await asyncio.to_thread(_EXPORTERS[fmt], suite)
        except Exception as exc:
            logger.exception("mcp export failed")
            return f"⚠️ Export to {fmt} failed: {exc}"
        await _audit(
            "mcp_export_suite", entity_id=suite_id, detail={"format": fmt, "path": path}
        )
        return shape_export_result(suite_id, fmt, path, len(suite.test_cases))
    except Exception as exc:
        logger.exception("handle_export_suite failed")
        return f"⚠️ Export failed: {exc}"


async def handle_bug_report(description: str, *, progress: ProgressCb = None) -> str:
    if _test_cases_only():
        return _TEST_CASES_ONLY_NOTICE
    description = (description or "").strip()
    if not description:
        return "⚠️ Describe the bug in plain language."
    try:
        await _emit(progress, "🐛 Formatting the bug report…")
        report = await generate_bug_report(description)
        await _audit("mcp_bug_report", detail={"chars": len(description)})
        return report
    except Exception as exc:
        logger.exception("handle_bug_report failed")
        return f"⚠️ Bug-report generation failed: {exc}"


async def handle_explore_step(
    feature: str,
    session_id: str,
    tester_response: str = "",
    *,
    progress: ProgressCb = None,
) -> str:
    if _test_cases_only():
        return _TEST_CASES_ONLY_NOTICE
    session_id = (session_id or "").strip()
    if not session_id:
        return "⚠️ Provide a stable session_id so the coach can track this session."
    try:
        feature = (feature or "").strip()
        sess = _SESSIONS.get(session_id)
        if sess is None:
            sess = {
                "feature": feature or "(unspecified feature)",
                "history": [],
                "memory": create_session_memory(),
            }
            _SESSIONS[session_id] = sess
        if feature:
            sess["feature"] = feature

        resp = (tester_response or "").strip()
        if resp:
            sess["history"].append({"role": "user", "content": resp})
            sess["memory"] = update_coverage(sess["memory"], resp)

        await _emit(progress, "🧭 Thinking of the next exploratory step…")
        raw = await coach_next_step(sess["feature"], sess["history"], sess["memory"])
        clean = strip_meta(raw)
        sess["history"].append({"role": "assistant", "content": clean})
        await _audit(
            "mcp_explore_step",
            entity_id=session_id,
            detail={"turns": sess["memory"].get("turn_count", 0)},
        )
        return shape_explore_step(session_id, sess, clean)
    except Exception as exc:
        logger.exception("handle_explore_step failed")
        return f"⚠️ Exploratory coaching failed: {exc}"


async def handle_search_corpus(
    query: str,
    entry_type: str = "test_case",
    feature: str = "",
    *,
    progress: ProgressCb = None,
) -> str:
    query = (query or "").strip()
    if not query:
        return "⚠️ Provide a search query."
    if not settings.qa_rag_enabled:
        return "ℹ️ The RAG corpus is disabled (set QA_RAG_ENABLED=true to enable corpus search)."
    try:
        result = await query_corpus(
            query,
            entry_type=(entry_type or "test_case"),
            top_k=settings.qa_rag_top_k,
            metadata_filter=(
                {"feature": feature.strip()} if (feature or "").strip() else None
            ),
        )
        if result.get("error"):
            return f"⚠️ Corpus search failed: {result['error']}"
        hits = result.get("content") or []
        await _audit(
            "mcp_search_corpus", detail={"query": query[:120], "entry_type": entry_type}
        )
        return shape_corpus_hits(query, hits)
    except Exception as exc:
        logger.exception("handle_search_corpus failed")
        return f"⚠️ Corpus search failed: {exc}"


async def handle_list_devices(*, progress: ProgressCb = None) -> str:
    try:
        await _emit(progress, "📱 Discovering connected devices…")
        result = await list_devices()
        if result.get("error"):
            return f"⚠️ Device discovery failed: {result['error']}"
        devices = result.get("content") or []
        await _audit("mcp_list_devices", detail={"count": len(devices)})
        return shape_devices(devices)
    except Exception as exc:
        logger.exception("handle_list_devices failed")
        return f"⚠️ Device discovery failed: {exc}"


async def handle_run_mobile_suite(
    mode: str,
    device_id: str = "",
    suite_id: str = "",
    app_id: str = "",
    goal: str = "",
    *,
    choose: ChooseCb = None,
    ask_text: AskCb = None,
    progress: ProgressCb = None,
) -> str:
    if _test_cases_only():
        return _TEST_CASES_ONLY_NOTICE
    mode = (mode or "").strip().lower()
    if not mode and settings.qa_mcp_elicit_enabled:
        picked = await _elicit_mobile_mode(choose)
        if picked.status == CHOSEN:
            mode = (picked.value or "").strip().lower()
        elif picked.status == DECLINED:
            return "👍 Cancelled — no mobile testing mode selected."
        else:
            return _mobile_mode_menu_markdown()
    if mode not in _MOBILE_MODES:
        return f"⚠️ Unknown mode '{mode}'. Choose one of: {', '.join(_MOBILE_MODES)}."
    if not settings.qa_maestro_enabled:
        return "ℹ️ Mobile testing is disabled (set QA_MAESTRO_ENABLED=true)."
    try:
        if mode == "export":
            suite_id = (suite_id or "").strip()
            if not suite_id and settings.qa_mcp_elicit_enabled:
                picked = await _elicit_suite(choose)
                if picked.status == CHOSEN:
                    suite_id = (picked.value or "").strip()
                elif picked.status == DECLINED:
                    return "👍 Cancelled — no suite selected."
            if not suite_id:
                return await _recent_suites_markdown("qa_run_mobile_suite")
            loaded = await load_suite(suite_id)
            suite = loaded.get("content")
            if suite is None:
                return f"⚠️ No stored suite with id `{suite_id}`."
            await _emit(progress, "📱 Writing Maestro flows…")
            path = await asyncio.to_thread(generate_maestro_flows, suite)
            await _audit(
                "mcp_run_mobile_suite",
                entity_id=suite_id,
                detail={"mode": mode, "path": path},
            )
            return shape_mobile_export(suite_id, path, len(suite.test_cases))

        device_id = (device_id or "").strip()
        if not device_id and settings.qa_mcp_elicit_enabled:
            picked = await _elicit_device(choose)
            if picked.status == CHOSEN:
                device_id = picked.value or ""
            elif picked.status == DECLINED:
                return "👍 Cancelled — no device selected."
            else:
                return await _device_menu_markdown()
        device = await _resolve_device(device_id)
        if device is None:
            return (
                f"⚠️ Device `{device_id}` not found. Run qa_list_devices to see "
                "connected devices, then pass a listed id."
            )

        if mode in ("run", "heal"):
            # run/heal read the flows written by mode "export" into the per-suite
            # dir flow_dir_for_suite(suite_id) (parity with app.py). Reject cleanly
            # when nothing was exported yet, rather than silently running an empty dir.
            if not (suite_id or "").strip() and settings.qa_mcp_elicit_enabled:
                picked = await _elicit_suite(choose)
                if picked.status == CHOSEN:
                    suite_id = (picked.value or "").strip()
                elif picked.status == DECLINED:
                    return "👍 Cancelled — no suite selected."
                # UNAVAILABLE falls through to the legacy global-flow-dir path.
            flow_path = _resolve_flow_path(suite_id)
            if (suite_id or "").strip() and (
                not flow_path or not Path(flow_path).exists()
            ):
                # Chainlit couples export -> run; mirror that so running a
                # freshly generated suite just works without a manual export.
                loaded = await load_suite(suite_id.strip())
                fresh_suite = loaded.get("content")
                if fresh_suite is not None:
                    await _emit(
                        progress, "📱 No flows yet — exporting the suite first…"
                    )
                    flow_path = await asyncio.to_thread(
                        generate_maestro_flows, fresh_suite
                    )
            if not flow_path or not Path(flow_path).exists():
                return (
                    f"⚠️ No Maestro flows found at `{flow_path}`. Export the suite "
                    'first with qa_run_mobile_suite(mode="export", suite_id=…), '
                    "then pass the same suite_id here."
                )
            audit_detail = {"mode": mode, "suite_id": (suite_id or "").strip()}

            if mode == "run":
                await _emit(progress, "📱 Running Maestro flows on the device…")
                res = await run_flows(device, flow_path=flow_path)
                if res.get("error"):
                    return f"⚠️ Maestro run failed: {res['error']}"
                await _audit(
                    "mcp_run_mobile_suite", entity_id=device["id"], detail=audit_detail
                )
                return shape_mobile_run(device["id"], res.get("content") or {})

            # mode == "heal"
            await _emit(progress, "🩹 Running the Maestro heal loop…")
            res = await heal_and_rerun(device, flow_path=flow_path)
            await _audit(
                "mcp_run_mobile_suite", entity_id=device["id"], detail=audit_detail
            )
            return shape_mobile_heal(device["id"], res.get("content") or {})

        # mode == "explore"
        if not (app_id or "").strip() and settings.qa_mcp_elicit_enabled:
            apps = (await list_installed_apps(device)).get("content") or []
            if apps:
                labels = [
                    f"{a.get('name') or a.get('id')} ({a.get('id')})" for a in apps[:15]
                ]
                by_label = {lab: a.get("id") for lab, a in zip(labels, apps[:15])}
                picked = await _elicit_choice(
                    choose, "Which app should I explore?", labels
                )
                if picked.status == CHOSEN:
                    app_id = by_label.get(picked.value or "") or ""
                elif picked.status == DECLINED:
                    return "👍 Cancelled — no app selected."
        if not (goal or "").strip() and settings.qa_mcp_elicit_enabled:
            asked = await _elicit_text(
                ask_text,
                "What should the exploration focus on? (e.g. 'the login flow')",
            )
            if asked.status == CHOSEN:
                goal = (asked.value or "").strip()
            elif asked.status == DECLINED:
                return "👍 Cancelled."
        await _emit(progress, "🧭 Starting the AI exploratory run…")
        res = await maestro_explore(device, goal or "Explore the app", app_id or "")
        await _audit(
            "mcp_run_mobile_suite", entity_id=device["id"], detail={"mode": mode}
        )
        return shape_mobile_explore(device["id"], res.get("content") or {})
    except Exception as exc:
        logger.exception("handle_run_mobile_suite failed")
        return f"⚠️ Mobile {mode} failed: {exc}"


_FA_MODES = ("jira", "mobile", "jira_mobile")
_FA_MODE_LABELS = {
    "Jira only": "jira",
    "Mobile only": "mobile",
    "Jira + Mobile": "jira_mobile",
}

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


def _tc_source_menu_markdown() -> str:
    """Markdown fallback when no source is given and elicitation is missing."""
    return (
        "## Where is the feature coming from?\n\n"
        "Call `qa_generate_test_cases` again with `feature_or_url` set to one "
        "of:\n"
        "- a **feature description** in plain language\n"
        "- a **Jira/issue URL**\n"
        "- a **web page URL** (the live UI is read)\n"
        "- a **Swagger/OpenAPI spec URL** (API test cases)\n\n"
        "For **mobile screens** (or Jira + mobile merged), call "
        "`qa_feature_analysis` with `mode=mobile` or `mode=jira_mobile` — "
        "I'll list connected devices and capture the screens."
    )


async def _guided_test_cases(
    *,
    choose: ChooseCb = None,
    ask_text: AskCb = None,
    progress: ProgressCb = None,
) -> str:
    """Guided source picker for test-case generation (Chainlit-starter parity).

    Asks where the feature comes from — describe / Jira / web URL / Swagger
    link / mobile screens / Jira + mobile — collects the missing input, and
    runs the full generation. Dialogs where the client supports elicitation;
    a markdown menu otherwise. Never raises."""
    source = await _elicit_choice(
        choose, "Where is the feature coming from?", list(_TC_SOURCE_LABELS)
    )
    if source.status == DECLINED:
        return "👍 Cancelled."
    src = _TC_SOURCE_LABELS.get(source.value or "", "")
    if source.status != CHOSEN or not src:
        # No choice dialogs — degrade to the simple typed-feature path.
        asked = await _elicit_text(
            ask_text,
            "Describe the feature to test (or paste a Jira / web / Swagger URL):",
        )
        if asked.status == CHOSEN and (asked.value or "").strip():
            return await handle_generate_test_cases(
                asked.value.strip(), progress=progress
            )
        if asked.status == DECLINED:
            return "👍 Cancelled."
        return _tc_source_menu_markdown()
    text = ""
    if src in _TC_SOURCE_PROMPTS:
        asked = await _elicit_text(ask_text, _TC_SOURCE_PROMPTS[src])
        if asked.status == DECLINED:
            return "👍 Cancelled."
        if asked.status != CHOSEN or not (asked.value or "").strip():
            return _tc_source_menu_markdown()
        text = asked.value.strip()
    images: list = []
    if src in ("mobile", "jira_mobile"):
        if not settings.qa_mobile_capture:
            if src == "mobile":
                return "ℹ️ Mobile capture is disabled (set QA_MOBILE_CAPTURE=true)."
            # jira_mobile continues from the ticket alone (parity with the
            # Chainlit wizard's capture-off fallback).
        else:
            picked_dev = await _elicit_device(choose)
            if picked_dev.status == DECLINED:
                return "👍 Cancelled — no device selected."
            if picked_dev.status != CHOSEN:
                return (
                    "⚠️ No connected devices found. Attach a device or boot an "
                    "emulator/simulator, then try again (check with "
                    "`qa_list_devices`)."
                )
            device = await _resolve_device(picked_dev.value or "")
            if device is None:
                return "⚠️ Device not found — run qa_list_devices and retry."
            screens, capture_error = await _fa_capture_screens(
                device, choose=choose, progress=progress
            )
            if capture_error and not screens:
                return f"⚠️ Couldn't capture a screenshot: {capture_error}"
            images = screens
    # Chainlit parity: the "Test cases" starter runs the Feature Analysis
    # wizard, which forces the report on and generates the full suite —
    # mirror that for every source.
    return await handle_generate_test_cases(
        text or "Feature captured from mobile device screens.",
        attached_images=images or None,
        force_feature_report=True,
        progress=progress,
    )


def _fa_mode_menu_markdown() -> str:
    """Markdown fallback when no mode is given and elicitation is unavailable."""
    return (
        "## Feature analysis\n\n"
        "Provide a feature description or a Jira/issue URL as `feature_or_url`, "
        "and call `qa_feature_analysis` with one of these as `mode`:\n"
        "- `jira` — analyse a feature description or Jira/issue URL\n"
        "- `mobile` — capture screens from a connected device (needs QA_MOBILE_CAPTURE)\n"
        "- `jira_mobile` — merge the ticket with captured screens"
    )


async def _fa_capture_screens(
    device: dict, *, choose: ChooseCb = None, progress: ProgressCb = None
) -> tuple:
    """Capture 1..N screenshots from *device*; returns (screens, error).

    Mirrors app.py's capture-another loop over MCP elicitation: after each
    capture the tester picks "Capture another screen" or "Generate the report",
    bounded by _MAX_ELICIT_ROUNDS. Without elicitation exactly one screen is
    captured. *screens* items match tools.image_description.describe_images
    input ({filename, mime, data}). An error on the FIRST capture is returned;
    a later failure just stops the loop with the screens gathered so far.
    """
    screens: list = []
    rounds = 0
    while rounds < _MAX_ELICIT_ROUNDS:
        rounds += 1
        label = device.get("name") or device.get("id")
        await _emit(progress, f"📸 Capturing screen {len(screens) + 1} from {label}…")
        shot = await capture_screenshot(device)
        if shot.get("error") or not shot.get("content"):
            return screens, (shot.get("error") or "no image returned")
        screens.append(
            {
                "filename": f"screen_{len(screens) + 1}.png",
                "mime": "image/png",
                "data": shot["content"],
            }
        )
        if not settings.qa_mcp_elicit_enabled or choose is None:
            break
        picked = await _elicit_choice(
            choose,
            f"Captured screen {len(screens)}. Capture another?",
            ["Capture another screen", "Generate the report"],
        )
        if picked.status != CHOSEN or picked.value != "Capture another screen":
            break
    return screens, ""


async def handle_feature_analysis(
    feature_or_url: str = "",
    mode: str = "",
    device_id: str = "",
    *,
    choose: ChooseCb = None,
    progress: ProgressCb = None,
) -> str:
    """Chainlit-parity Feature Analysis: mode menu (jira / mobile / jira_mobile),
    device pick + capture-another screenshot loop on the mobile modes, vision
    descriptions via describe_images, and the merged report via analyze_feature.
    A plain call with only feature_or_url (no mode, no elicitation) behaves like
    the original single-shot text/Jira analysis."""
    if not settings.qa_feature_analysis_enabled:
        return "ℹ️ Feature Analysis is disabled (set QA_FEATURE_ANALYSIS_ENABLED=true)."
    text = (feature_or_url or "").strip()
    mode = (mode or "").strip().lower().replace("+", "_").replace(" ", "_")
    if mode == "jira_and_mobile":
        mode = "jira_mobile"

    if not mode:
        if settings.qa_mcp_elicit_enabled:
            picked = await _elicit_choice(
                choose, "Which Feature Analysis mode?", list(_FA_MODE_LABELS)
            )
            if picked.status == CHOSEN:
                mode = _FA_MODE_LABELS.get(picked.value or "", "")
            elif picked.status == DECLINED:
                return "👍 Cancelled — no Feature Analysis mode selected."
        if not mode:
            if text:
                mode = "jira"  # single-shot back-compat when elicitation is off
            else:
                return _fa_mode_menu_markdown()
    if mode not in _FA_MODES:
        return f"⚠️ Unknown mode '{mode}'. Choose one of: {', '.join(_FA_MODES)}."
    if mode in ("jira", "jira_mobile") and not text:
        return "⚠️ Provide a feature description or a Jira/issue URL as feature_or_url."

    try:
        jira_text = ""
        used_url = False
        if mode in ("jira", "jira_mobile") and _is_url(text):
            await _emit(progress, "🔗 Fetching the ticket…")
            url_content = await fetch_url_content(text)
            used_url = True
            if not url_content.get("error"):
                jira_text = url_content.get("content") or ""

        screen_descriptions = ""
        screens_captured = 0
        if mode in ("mobile", "jira_mobile"):
            if not settings.qa_mobile_capture:
                if mode == "mobile":
                    return "ℹ️ Mobile capture is disabled (set QA_MOBILE_CAPTURE=true)."
                # jira_mobile continues from the ticket alone (parity with app.py)
            else:
                device_id = (device_id or "").strip()
                if not device_id and settings.qa_mcp_elicit_enabled:
                    picked = await _elicit_device(choose)
                    if picked.status == CHOSEN:
                        device_id = picked.value or ""
                    elif picked.status == DECLINED:
                        return "👍 Cancelled — no device selected."
                    else:
                        return await _device_menu_markdown(tool="qa_feature_analysis")
                device = await _resolve_device(device_id)
                if device is None:
                    return (
                        f"⚠️ Device `{device_id}` not found. Run qa_list_devices to "
                        "see connected devices, then pass a listed id."
                    )
                screens, capture_error = await _fa_capture_screens(
                    device, choose=choose, progress=progress
                )
                if capture_error and not screens:
                    return f"⚠️ Couldn't capture a screenshot: {capture_error}"
                screens_captured = len(screens)
                if screens:
                    await _emit(
                        progress,
                        f"🖼️ Describing {screens_captured} captured screen(s)…",
                    )
                    screen_descriptions = await describe_images(screens)

        feature_text = text or "Feature captured from mobile device screens."
        await _emit(progress, "📋 Analyzing the feature…")
        report = await analyze_feature(
            feature_text, jira_text, screen_descriptions, None, []
        )
        markdown = render_report_markdown(report, compact=True)
        await _audit(
            "mcp_feature_analysis",
            detail={
                "mode": mode,
                "source": "url" if used_url else "text",
                "screens": screens_captured,
            },
        )
        header = "## Feature Analysis\n\n"
        if mode != "jira":
            header += f"_Mode: {mode} · screens captured: {screens_captured}_\n\n"
        return header + markdown
    except Exception as exc:
        logger.exception("handle_feature_analysis failed")
        return f"⚠️ Feature analysis failed: {exc}"


# --------------------------------------------------------------------------- #
# Guided wizard (choice-driven UX; markdown fallback when elicitation is off)  #
# --------------------------------------------------------------------------- #

# Each non-mobile branch maps a menu option to the tool the client should call
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
        "Mobile testing",
    ]


def _wizard_menu_markdown(options: list) -> str:
    lines = [
        "## QA wizard",
        "",
        "Pick what you'd like to do, then call the matching tool:",
        "",
    ]
    for opt in options:
        if opt == "Mobile testing":
            lines.append(
                "- **Mobile testing** — call `qa_run_mobile_suite` "
                "(I'll ask for the mode and device)."
            )
            continue
        tool, hint = _WIZARD_HANDOFFS[opt]
        lines.append(f"- **{opt}** — call `{tool}`. {hint}")
    return "\n".join(lines)


def _wizard_handoff_markdown(choice: str) -> str:
    tool, hint = _WIZARD_HANDOFFS[choice]
    return f"## {choice}\n\nGreat — call `{tool}` next. {hint}"


async def handle_wizard(
    *,
    choose: ChooseCb = None,
    ask_text: AskCb = None,
    progress: ProgressCb = None,
) -> str:
    """Entry-point guided menu mirroring the Chainlit starters.

    With full elicitation (choice dialogs + free-text input) the wizard runs
    the chosen flow END-TO-END: it collects the missing input, invokes the real
    handler, and returns the finished result — a non-technical tester never
    needs tool names or parameters. Without free-text support it degrades to
    the concise handoff telling the client which tool to call next; without
    any elicitation it returns the markdown menu. Never raises."""
    await _audit("mcp_wizard")
    try:
        options = _wizard_options()
        top = await _elicit_choice(choose, "What would you like to do?", options)
        if top.status == UNAVAILABLE:
            return _wizard_menu_markdown(options)
        if top.status == DECLINED:
            return "👍 No problem — nothing selected. Call `qa_wizard` again anytime."
        choice = top.value

        if choice == "Generate test cases":
            return await _guided_test_cases(
                choose=choose, ask_text=ask_text, progress=progress
            )

        if choice == "Report a bug":
            asked = await _elicit_text(ask_text, "Describe the bug in plain language:")
            if asked.status == CHOSEN and (asked.value or "").strip():
                return await handle_bug_report(asked.value.strip(), progress=progress)
            if asked.status == DECLINED:
                return "👍 Cancelled."
            return _wizard_handoff_markdown(choice)

        if choice == "Exploratory testing coach":
            asked = await _elicit_text(
                ask_text, "Which feature or area do you want to explore?"
            )
            if asked.status == CHOSEN and (asked.value or "").strip():
                feature = asked.value.strip()
                # Deterministic id: running the wizard again with the same
                # feature continues the same coaching session.
                session_id = (
                    "wizard-"
                    + hashlib.sha1(feature.lower().encode("utf-8")).hexdigest()[:10]
                )
                step = await handle_explore_step(feature, session_id, progress=progress)
                return step + (
                    "\n\n_To continue: run `qa_wizard` again with the same "
                    "feature, or call `qa_explore_step` with session_id "
                    f"`{session_id}` and your `tester_response`._"
                )
            if asked.status == DECLINED:
                return "👍 Cancelled."
            return _wizard_handoff_markdown(choice)

        if choice == "Mobile testing":
            picked = await _elicit_mobile_mode(choose)
            if picked.status == UNAVAILABLE:
                return _mobile_mode_menu_markdown()
            if picked.status == DECLINED:
                return "👍 Cancelled — no mobile mode selected."
            # Run the real flow — device / suite / app / goal are elicited inside.
            return await handle_run_mobile_suite(
                picked.value or "",
                choose=choose,
                ask_text=ask_text,
                progress=progress,
            )

        if choice in _WIZARD_HANDOFFS:
            return _wizard_handoff_markdown(choice)
        # Unrecognised selection — fall back to the full menu.
        return _wizard_menu_markdown(options)
    except Exception as exc:
        logger.exception("handle_wizard failed")
        return f"⚠️ Wizard failed: {exc}"


def _binary_line(name: str) -> str:
    path = shutil.which(name)
    return f"- {'✅' if path else '❌'} `{name}`" + (
        f" — {path}" if path else " — not found"
    )


async def handle_setup_check(*, progress: ProgressCb = None) -> str:
    """Machine-readiness report for tester onboarding: Python, LLM backend
    auth, mobile tooling, connected devices, and feature gates. Read-only and
    never raises — the first thing to run on a new machine."""
    await _audit("mcp_setup_check")
    try:
        from llm import check_backend
        from tools.updater import _INSTALL_DIR, _local_version

        update_note = ""
        if _test_cases_only() and _DIST_UPDATE_REPO:
            from tools.updater import run_update_check

            await _emit(progress, "⬆️ Checking for the latest release…")
            update_status = await asyncio.to_thread(
                run_update_check,
                force=True,
                repo_override=_DIST_UPDATE_REPO,
                lock_override=True,
            )
            if update_status in ("updated", "healed"):
                update_note = (
                    "> 🔄 **A new version was just installed.** The server is "
                    "reloading now — run `qa_setup_check` again in ~10 seconds "
                    "to see it."
                )
                _schedule_reload()
        await _emit(progress, "🔎 Checking the LLM backend…")
        ok, warning = check_backend()
        backend = (settings.qa_llm_backend or "cli").strip().lower()
        app_version = _local_version(_INSTALL_DIR)
        lines = [
            "## Setup check",
            "",
            *([f"**App version:** v{app_version}", ""] if app_version else []),
            *([update_note, ""] if update_note else []),
            f"**Python:** {sys.version.split()[0]}",
            f"**LLM backend:** `{backend}` — "
            + ("✅ ready" if ok else f"❌ {warning}"),
            "",
            "**Tooling:**",
            _binary_line("cursor-agent"),
            # maestro only drives on-device runs — not part of the dist edition.
            *([] if _test_cases_only() else [_binary_line("maestro")]),
            _binary_line("adb"),
            _binary_line("xcrun"),
            "",
        ]
        await _emit(progress, "📱 Scanning for devices…")
        devices = (await list_devices()).get("content") or []
        lines.append(f"**Devices connected:** {len(devices)}")
        for dev in devices[:6]:
            lines.append(
                f"- `{dev.get('id')}` — {dev.get('name')} "
                f"({dev.get('platform')}/{dev.get('kind')})"
            )
        lines.append("")
        lines.append("**Feature gates:**")
        gates = [
            (
                "Feature Analysis (QA_FEATURE_ANALYSIS_ENABLED)",
                settings.qa_feature_analysis_enabled,
            ),
            ("Mobile capture (QA_MOBILE_CAPTURE)", settings.qa_mobile_capture),
            (
                "Swagger/OpenAPI links (QA_SWAGGER_ENABLED)",
                settings.qa_swagger_enabled,
            ),
        ]
        if not _test_cases_only():
            gates += [
                ("Mobile testing (QA_MAESTRO_ENABLED)", settings.qa_maestro_enabled),
                ("Maestro dry-run (QA_MAESTRO_DRY_RUN)", settings.qa_maestro_dry_run),
                (
                    "AI exploratory (QA_MAESTRO_EXPLORE_ENABLED)",
                    settings.qa_maestro_explore_enabled,
                ),
                (
                    "Self-heal (QA_MAESTRO_HEAL_ENABLED)",
                    settings.qa_maestro_heal_enabled,
                ),
            ]
        gates += [
            ("RAG corpus (QA_RAG_ENABLED)", settings.qa_rag_enabled),
            ("Wizard dialogs (QA_MCP_ELICIT_ENABLED)", settings.qa_mcp_elicit_enabled),
        ]
        for label, value in gates:
            lines.append(f"- {'✅' if value else '⬜'} {label}")
        if not ok:
            lines += [
                "",
                "> ⚠️ Fix the LLM backend first — nothing generates without it. "
                + warning,
            ]
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("handle_setup_check failed")
        return f"⚠️ Setup check failed: {exc}"
