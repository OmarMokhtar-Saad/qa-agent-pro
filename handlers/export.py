"""Moved from handlers (refactor P2): export."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Callable

from agents.host_orchestration import _dedup_review_on
from agents.host_submission import safe_prep_id as host_submission_safe_prep_id
from config.settings import settings
from handlers import _common, _elicit, _reply
from tools import telemetry
from tools.csv_exporter import generate_test_case_csv
from tools.gherkin_exporter import generate_feature_file
from tools.playwright_exporter import generate_playwright_script
from tools.suite_store import load_suite
from tools.testrail_exporter import generate_testrail_csv
from tools.xlsx_generator import generate_test_case_xlsx

logger = logging.getLogger("tools.mcp_handlers")

# ops-6 (bug 3): cap on an elicited folder answer before it is treated as a path.
_MAX_EXPORT_DIR_CHARS = 400

# F2 (2026-08-10): the folder words a tester actually types. A bare relative
# answer is never resolved (it would land inside the install dir); the matching
# entry is offered back as the full path they meant.
_WELL_KNOWN_FOLDERS = {
    "desktop": "~/Desktop",
    "documents": "~/Documents",
    "downloads": "~/Downloads",
}


# The destination a REJECTED save-folder answer really falls back to. BOTH call
# sites land in the secure temp dir: _auto_export_xlsx only ASKS when
# _resolved_export_dir() is empty, and handle_export_suite's five single-file
# exporters each write into <tempdir>/qa_agents_exports/ when no directory is
# threaded into them.
#
# 2026-08-15 (dead-code deletion batch D4): this used to be wrapped in a
# _default_export_label(fmt) helper, because the Zephyr PAIR was the ONE export
# handed the configured directory and therefore the one format whose rejection
# note named a different folder. tools/zephyr_exporter.py is DELETED, so every
# surviving format falls back here and the helper was a constant-returning
# branch over nothing. Both call sites interpolate this name directly. If a
# format is ever added that writes somewhere else, bring the per-format helper
# back WITH a test -- do not quietly widen this constant.
_TEMP_EXPORT_LABEL = "a secure temp folder"


def _safe_elicited_dir(
    answer: str,
    sentinel_ok: bool = False,
    fallback_label: str = _TEMP_EXPORT_LABEL,
) -> tuple[str, str]:
    """Validate an elicited save-folder answer BEFORE it becomes a real path.

    Returns ``(directory_or_empty, note)``. An empty directory means "rejected --
    keep the default destination", and *note* is always non-empty in that case so
    the tester is told, rather than silently getting a different location.

    *fallback_label* NAMES that destination inside every rejection note: only the
    caller knows where its own default write lands, and a note that claims a
    folder the file did not go to is worse than no note at all -- the tester goes
    looking in an empty directory. The reply prints the real path beside it.

    WHY: the answer is UNTRUSTED host-model text, and it used to be passed
    straight to ``Path(...).mkdir(parents=True)``. On 2026-07-29 a host echoed
    back the raw `.env` line -- inline comment included -- and the server created
    ``data/exports       # save auto-exported .xlsx here (... sessions/`` plus an
    ``updates)`` subdirectory, splitting on the `/` inside the comment, then
    reported that path to the tester as the location of their file.

    Rules: no comment/newline/NUL characters (a comment marker means the answer
    is a config line, not a folder), a length cap, and the resolved path must sit
    under the install dir, the user's home, or the system temp dir -- so an
    answer like ``~/../../etc`` cannot direct writes anywhere it likes. Never
    raises: any failure rejects the answer and keeps the default.
    """
    raw = (answer or "").strip()
    if not raw:
        return "", ""
    screened = _screen_elicited_dir(raw, sentinel_ok, fallback_label)
    if screened is not None:
        return screened
    try:
        return _resolve_elicited_dir(raw, fallback_label)
    except Exception:
        logger.debug("elicited export dir rejected", exc_info=True)
        return "", (
            "\n> ℹ️  The folder you replied with could not be used, "
            f"so {fallback_label} was used instead."
        )


def _screen_elicited_dir(
    raw: str, sentinel_ok: bool, fallback_label: str
) -> tuple[str, str] | None:
    """Text-only checks of `_safe_elicited_dir`; None means "go on to the path checks"."""
    if sentinel_ok and raw.lower() == "default":
        # K3 (2026-08-10): Cursor will not submit an EMPTY elicitation value, so
        # the prompt's old "leave blank for the default" was unreachable and the
        # prompt now offers the word `default` instead. Without this branch that
        # word falls through to the not-a-full-path rejection below and the tester
        # is told their answer "is not a full path" -- true, but a non-sequitur for
        # an answer the prompt asked them to give. Returning ("", "") is the
        # keep-the-configured-default path, silently, which is what they asked for.
        # sentinel_ok is opt-in so qa_export_suite(output_dir="default") -- a real
        # parameter, never prompted -- keeps its corrective note.
        return "", ""
    if len(raw) > _MAX_EXPORT_DIR_CHARS:
        return "", (
            "\n> ℹ️  The folder you replied with was too long to be a real path, "
            f"so {fallback_label} was used instead."
        )
    if any(ch in raw for ch in ("#", "\n", "\r", "\x00")):
        return "", (
            "\n> ℹ️  The folder you replied with looks like a config line rather "
            "than a folder (it contains a comment marker or a line break), "
            f"so {fallback_label} was used instead."
        )
    return None


def _not_full_path_note(raw: str, fallback_label: str) -> str:
    """Rejection note for an elicited answer that is not an absolute path."""
    suggestion = _WELL_KNOWN_FOLDERS.get(raw.lower())
    if suggestion:
        return (
            "\n> ℹ️  I need the FULL path of that folder, so "
            f"{fallback_label} was used instead. Reply "
            f"`{suggestion}` if that is the one you meant."
        )
    return (
        "\n> ℹ️  The folder you replied with is not a full path "
        "(it would have been read relative to the server's own folder), "
        f"so {fallback_label} was used instead."
    )


def _hidden_folder_note(resolved: Path, roots: list[Path], fallback_label: str) -> str:
    """Rejection note when *resolved* is in a dot-directory under a root, else ''."""
    # A dot-directory (~/.ssh, ~/.aws, ~/.config) is no place for a deliverable.
    for root in roots:
        if root in resolved.parents and any(
            part.startswith(".") for part in resolved.relative_to(root).parts
        ):
            return (
                "\n> ℹ️  The folder you replied with is inside a hidden "
                f"folder, so {fallback_label} was used instead."
            )
    return ""


def _resolve_elicited_dir(raw: str, fallback_label: str) -> tuple[str, str]:
    """Path half of `_safe_elicited_dir`: absolute check, then the allowed roots.

    May raise; the caller turns any failure into a rejection note."""
    import tempfile

    from tools.updater import _INSTALL_DIR

    # F2 (2026-08-10): an ELICITED answer is free-text a tester typed, so a
    # bare word like "desktop" must never be resolved against the process
    # CWD -- that is the install dir, which is itself an allowed root, so the
    # check below APPROVED it and the deliverable landed inside the
    # installation. Only absolute / ~-rooted answers reach that check.
    if not Path(raw).expanduser().is_absolute():
        return "", _not_full_path_note(raw, fallback_label)

    resolved = Path(raw).expanduser().resolve()
    roots = [
        Path(_INSTALL_DIR).resolve(),
        Path.home().resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]
    if not any(resolved == r or r in resolved.parents for r in roots):
        return "", (
            "\n> ℹ️  The folder you replied with is outside the install "
            "folder, your home folder and the temp folder, "
            f"so {fallback_label} was used instead."
        )
    install = roots[0]
    if resolved == install:
        return "", (
            "\n> ℹ️  The folder you replied with is the install folder itself, "
            f"so {fallback_label} was used instead."
        )
    if install not in resolved.parents:
        hidden = _hidden_folder_note(resolved, roots[1:], fallback_label)
        if hidden:
            return "", hidden
    return str(resolved), ""


def _code_changed_since_start() -> bool:
    """True when the installed VERSION differs from the one this process loaded.

    Content-based, not mtime-based: a touched-but-identical VERSION must not
    trigger a reload loop. Never raises -- an unreadable VERSION reads as
    unchanged, so a failure can only SKIP a reload, never cause a spurious one.
    """
    try:
        from tools.updater import _INSTALL_DIR, _local_version

        on_disk = _local_version(Path(_INSTALL_DIR))
        return bool(
            on_disk and _common._BOOT_VERSION and on_disk != _common._BOOT_VERSION
        )
    except Exception:
        logger.debug("could not compare the installed version", exc_info=True)
        return False


def _env_changed_since_start() -> bool:
    """True when the install's .env carries DIFFERENT settings than this process
    booted with.

    config/settings parses .env exactly once at startup, so an edit made while
    the server is running has NO effect until the process is replaced. Callers
    use this to schedule the reload that applies it.

    CONTENT, not mtime (2026-08-09). Two things rewrite a running install's .env
    without changing a single setting: updater.migrate_env(), which appends a
    dated banner of newly shipped keys after an update, and env_heal.heal_env(),
    which qa-doctor runs. Under the mtime check EITHER rewrite looked like a
    tester edit, so the server scheduled a reload for work it had just done to
    itself. This is the same fix Batch A applied to the dist launcher's watchdog
    (`_env_fingerprint` in scripts/build_dist.LAUNCHER_TEMPLATE) and the rule
    `_code_changed_since_start` already applies to VERSION.

    Never raises: a missing or unreadable .env fingerprints as "" and reads as
    unchanged, so a failure here can only ever SKIP a reload, never trigger a
    spurious one.
    """
    current = _common._env_fingerprint()
    if not current:
        return False
    return current != _common._BOOT_ENV_FINGERPRINT


def _edition_facts() -> dict:
    """The edition facts ``qa_machine_report`` injects into its backend section.

    One dict expression, so a raising fact gives no dict at all (the report
    then shows ``{}``) rather than half of one. Reads both functions as module
    globals at call time, so a test patch on either is what the reply carries.
    """
    return {
        "test_cases_only": bool(_common._test_cases_only()),
        "mobile_modules_present": bool(_common._mobile_modules_present()),
    }


_TEST_CASES_ONLY_NOTICE = (
    "⚠️ This edition generates test cases only — this tool is not available. "
    "Use qa_generate_test_cases or qa_export_suite."
)

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


def _available_exporters() -> dict[str, Callable]:
    """The export-format map for this call.

    Built from the module-global ``_EXPORTERS`` at CALL time, so
    ``monkeypatch.setitem(mcp_handlers._EXPORTERS, ...)`` keeps working. Never
    raises.

    2026-08-15 (dead-code deletion batch D4): this used to take ``story_key`` /
    ``output_dir`` and add a sixth, gate-controlled ``zephyr`` entry -- the only
    exporter that wrote a PAIR into a directory rather than a single file, and
    the only reason those two parameters existed. ``tools/zephyr_exporter.py``
    is DELETED, so the map is now exactly ``_EXPORTERS`` and the parameters went
    with the entry instead of being kept as arguments nothing reads.

    NOTE: mcp_server.py's qa_export_suite docstring is the only format list an
    MCP client ever sees, so it must stay a superset of these keys.
    """
    return dict(_EXPORTERS)


def _format_menu_markdown() -> str:
    return (
        "## Export format\n\n"
        "Call `qa_export_suite` with one of these as `format`:\n"
        + "\n".join(f"- `{f}`" for f in sorted(_available_exporters()))
    )


def _resolved_export_dir() -> str:
    """``settings.qa_export_dir`` as an ABSOLUTE path.

    The configured default ("data/exports") is RELATIVE, so it used to resolve
    against whatever working directory the MCP client happened to launch the
    server with: the same install therefore printed a different path in Claude
    Desktop, Claude Code and Cursor, and a non-technical tester could not find
    their own deliverable. Anchor a relative value to the install root instead.

    "" (the legacy secure-temp behavior) stays "". Never raises -- a failure
    degrades to the configured value as-is, which is exactly today's behavior.
    """
    try:
        raw = (settings.qa_export_dir or "").strip()
        if not raw:
            return ""
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = _common._INSTALL_ROOT / path
        return str(path)
    except Exception:
        logger.debug("resolving qa_export_dir failed -- using it as-is", exc_info=True)
        return (settings.qa_export_dir or "").strip()


def _export_ticket_slug(source_url: str) -> str:
    """A Jira issue key for use inside an export FILENAME, or "".

    F11 (2026-08-15): three suites exported into one folder were
    ``qa_test_cases_<suite_id>_<stamp>.xlsx`` and nothing else, so a tester
    holding three tickets had to open all three to tell them apart.

    FILESYSTEM SAFETY is the whole reason this goes through
    ``jira_mcp.issue_key_from_url`` rather than any part of the raw URL: that
    helper is regex-gated to ``[A-Z][A-Z0-9]*-[0-9]+`` (``_valid_issue_key``),
    so the value spliced into a path cannot contain a separator, a traversal
    sequence, a NUL, or anything else needing escaping -- it is re-checked here
    anyway rather than trusted, because this function's output becomes a path.
    Returns "" for a non-Jira source, which restores the previous filename
    byte-for-byte. Never raises."""
    try:
        from tools.jira_mcp import issue_key_from_url

        key = str(issue_key_from_url(str(source_url or "")) or "").strip()
        if not key or len(key) > 32:
            return ""
        if not all(ch.isalnum() or ch == "-" for ch in key):
            return ""
        return key
    except Exception:  # pragma: no cover - defensive; a filename must never raise
        logger.debug("export ticket slug failed", exc_info=True)
        return ""


def _unanswered_dir_note(asked) -> str:
    """The note for a save-folder question that got no answer, per cause."""
    # The two causes get DIFFERENT sentences. Saying "no answer arrived
    # within 55s" when no dialog was ever shown -- which is what a spent
    # budget means -- would be untrue, and _auto_export_xlsx is reached
    # at the very end of a full generation, so that is the likely case
    # on the generate path rather than an exotic one.
    if asked.timed_out:
        logger.warning(
            "auto-export: no save-folder answer within %ss -- using the "
            "default export folder",
            int(_elicit._ELICIT_TIMEOUT_S),
        )
        return (
            "\n> ℹ️  No answer to the save-folder question arrived "
            f"within {int(_elicit._ELICIT_TIMEOUT_S)}s, so the default export "
            "folder was used."
        )
    logger.warning(
        "auto-export: save-folder dialog skipped (per-call "
        "elicitation budget spent) -- using the default folder"
    )
    return (
        "\n> ℹ️  This call had already run past its interactive "
        "budget, so the save-folder question was not asked and the "
        "default export folder was used."
    )


async def _auto_export_ask_dir(
    ask_text: _elicit.AskCb, progress: _common.ProgressCb
) -> tuple[str, str]:
    """Ask the tester where to save; returns (export_dir, reject_note)."""
    export_dir = ""
    reject_note = ""
    await _common._emit(progress, "📂 Asking where to save the Excel file…")
    # K1 (2026-08-10): the outer asyncio.wait_for F1b added here is gone --
    # _elicit_text bounds itself now, so wrapping it again would just be a
    # second, looser bound on the same await.
    #
    # "leave blank" was a LIE: Cursor renders the dialog with a REQUIRED
    # Value* field and will not submit an empty answer, so the advertised
    # optional path was unreachable. The word `default` is reachable, and
    # _safe_elicited_dir(sentinel_ok=True) understands it.
    asked = await _elicit._elicit_text(
        ask_text,
        "Where should the Excel file be saved? Reply with a full folder "
        "path, or reply `default` to use the default (a secure temp "
        f"folder). No answer within {int(_elicit._ELICIT_TIMEOUT_S)}s also uses "
        "the default.",
    )
    if _common._unanswered(asked):
        reject_note = _unanswered_dir_note(asked)
    if asked.status == _common.CHOSEN and (asked.value or "").strip():
        # ops-6 (bug 3): UNTRUSTED host text -- validate before it
        # becomes a real directory. A rejected answer keeps the
        # configured default AND says so.
        # export_dir is EMPTY in this branch (the dialog is only
        # asked when the configured folder did not resolve), so a
        # rejected answer falls back to the secure temp dir -- and
        # the note has to name that one, not a configured folder.
        picked, why = _safe_elicited_dir(
            asked.value,
            sentinel_ok=True,
            fallback_label=_TEMP_EXPORT_LABEL,
        )
        if picked:
            export_dir = picked
        elif why:
            reject_note = why
    return export_dir, reject_note


def _auto_export_target(suite, export_dir: str, source_url: str) -> str | None:
    """The workbook path inside export_dir, or None when it is unusable."""
    try:
        dest = Path(export_dir).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        frag = (getattr(suite, "suite_id", "") or "suite")[:16]
        stamp = time.strftime("%Y%m%d_%H%M%S")
        # F11: ticket key FIRST, because that is what a tester scans a
        # folder for. The `qa_test_cases_` prefix is kept (every
        # cleanup_temp_files glob keys off it) and so are the suite_id
        # fragment and the timestamp, so uniqueness and every existing
        # consumer are unchanged. "" for a non-Jira source -> the
        # previous name exactly.
        ticket = _export_ticket_slug(source_url)
        return str(
            (
                dest
                / (
                    f"qa_test_cases_{ticket}_{frag}_{stamp}.xlsx"
                    if ticket
                    else f"qa_test_cases_{frag}_{stamp}.xlsx"
                )
            ).resolve()
        )
    except OSError:
        logger.warning(
            "qa_export_dir %r unusable -- falling back to temp export",
            export_dir,
            exc_info=True,
        )
        return None


async def _auto_export_write(
    suite, output_path: str | None, on_path: Callable[[str], None] | None
) -> str:
    """Write the workbook (explicit path or secure temp) and hand on the path."""
    if output_path is not None:
        path = await asyncio.to_thread(generate_test_case_xlsx, suite, output_path)
    else:
        path = await asyncio.to_thread(_EXPORTERS["xlsx"], suite)
    if on_path is not None:
        # Hands the caller the path the tester actually got,
        # including an elicited custom folder. Added so the
        # Zephyr pair could land beside the workbook; that
        # exporter was DELETED on 2026-08-15 (batch D4) and
        # handle_submit_suite still reports the path.
        try:
            on_path(path)
        except Exception:
            logger.debug("auto-export on_path callback failed", exc_info=True)
    return path


async def _auto_export_reply(path: str, reject_note: str) -> str:
    """The reply block for a written export: the path, a link and how to open it."""
    try:
        uri = Path(path).as_uri()
    except (ValueError, OSError):
        uri = ""
    await _common._audit("mcp_auto_export_xlsx", detail={"path": path})
    link = f"[Open the file]({uri})\n\n" if uri else ""
    # This path IS the deliverable, and the only channel an MCP tool result
    # has for it is TEXT -- so it has to survive a chat model that
    # paraphrases the reply instead of quoting it. Hence the explicit
    # show-this-verbatim instruction, and hence the callers placing this
    # block FIRST in their response rather than after the suite body: on
    # 2026-08-03 a real run generated 98 cases and exported cleanly, and the
    # tester never saw a path.
    return (
        "### \U0001f4c4 Your Excel file is ready\n\n"
        "**\U0001f4c2 SHOW THIS PATH TO THE TESTER, VERBATIM \u2014 it is "
        "the deliverable they asked for:**\n\n"
        f"`{path}`\n\n"
        f"{link}"
        "Open it (macOS):\n\n"
        f'```bash\nopen "{path}"\n```\n\n'
        "_That spreadsheet is the finished deliverable — nothing else to "
        "run. Need CSV, Gherkin, Playwright or TestRail instead? Just say "
        "so._" + reject_note
    )


async def _auto_export_xlsx(
    suite,
    ask_text: _elicit.AskCb = None,
    on_path: Callable[[str], None] | None = None,
    progress: _common.ProgressCb = None,
    source_url: str = "",
) -> str:
    """Best-effort Excel auto-export for QA_AUTO_EXPORT_XLSX (MCP path only).

    Reuses generate_test_case_xlsx so the cell_sanitizer formula-injection
    protection applies identically. When elicitation dialogs are available
    (QA_MCP_ELICIT_ENABLED + a capable client) the tester is first asked
    where to save the file; a declined/blank/unavailable answer keeps the
    configured default. When settings.qa_export_dir is set the file
    lands in that stable folder under the qa_test_cases_*.xlsx naming so a
    non-technical tester can find and re-open a persistent deliverable;
    otherwise it keeps the legacy secure-temp behavior via the
    _EXPORTERS["xlsx"] path exactly as qa_export_suite uses it. NEVER raises: an
    unusable directory falls back to temp, and any exporter error returns a
    warning note instead of breaking the already-generated (and persisted)
    suite result.

    The exporter is called via the module global (direct call for the
    explicit-path branch, the _EXPORTERS["xlsx"] lambda for the temp branch),
    so monkeypatch.setattr(mcp_handlers, "generate_test_case_xlsx", ...)
    intercepts BOTH branches at call time.
    """
    try:
        await _common._emit(progress, "📄 Writing the Excel export…")
        output_path = None
        export_dir = _resolved_export_dir()
        reject_note = ""
        if _common._elicit_enabled() and ask_text is not None and not export_dir:
            # F1a (2026-08-10): ASK ONLY WHEN THERE IS NO ANSWER ALREADY. With
            # QA_EXPORT_DIR resolved (the shipped default always resolves) this
            # dialog held qa_submit_suite -- a FINISHED, already-persisted suite
            # -- open on a client that may never answer, and the retried submit
            # then produced a second suite. An empty export_dir is the legacy
            # secure-temp install, where the tester's answer is the only signal.
            export_dir, reject_note = await _auto_export_ask_dir(ask_text, progress)
        if export_dir:
            output_path = _auto_export_target(suite, export_dir, source_url)
        path = await _auto_export_write(suite, output_path, on_path)
        return await _auto_export_reply(path, reject_note)
    except Exception as exc:
        logger.exception("mcp auto-export xlsx failed")
        return (
            f"⚠️ Auto-export to Excel failed: {exc} — you can still "
            "export the suite with `qa_export_suite`."
        )


def _finalize_route_phrase(short: bool = False) -> str:
    """How to finalize, in one place, resolved on the live seam.

    Five separate replies used to spell this out, and with the duplicate review
    ON, four of them named the route that FORFEITS it and never mentioned the
    sidecar -- one calling it "the route here". Each was correct when written
    and none was wrong in isolation; the defect was that five authors could
    disagree. agents/host_*.py owns which route is right (it gets both seam
    states right, and _FINALIZE_SENTINEL exists because run3/TICKET-5645 lost the
    review across 98 cases by leading with the empty one), so callers ask here
    rather than stating it.

    ``short`` gives the clause form for mid-sentence use. Never raises.
    """
    try:
        review_on = _dedup_review_on()
    except Exception:  # pragma: no cover - a reply must never break on this
        logger.debug("finalize-route seam unreadable", exc_info=True)
        review_on = False
    if not review_on:
        return (
            "an EMPTY `suite_json`"
            if short
            else "finalize with `qa_submit_suite` and an EMPTY `suite_json`"
        )
    clause = (
        "a small JSON SIDECAR carrying `duplicate_groups` (an empty list if "
        "you found none) and NO test_cases -- that KEEPS the duplicate "
        "review; an EMPTY `suite_json` also finalizes but FORFEITS it"
    )
    return clause if short else f"finalize with `qa_submit_suite` and {clause}"


def _relocate_export(path: str, target_dir: str) -> tuple[str, str]:
    """Move a written export into the folder the tester named.

    Returns ``(path, note)``. The note is non-empty ONLY when the move failed,
    and in that case the ORIGINAL path comes back, so the reply always points at
    a file that exists. Never raises.

    WHY a move rather than an output path: the five single-file exporters each
    choose their own filename and extension, so threading a directory into them
    would mean a per-format extension table that can silently drift. The Zephyr
    exporter was the one exception -- it wrote a PAIR into a directory and took
    the folder directly -- and it was DELETED on 2026-08-15 (dead-code
    deletion batch D4), so every surviving format is relocated by this function.
    """
    try:
        src = Path(path)
        dest_dir = Path(target_dir).expanduser()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if src.resolve() == dest.resolve():
            return str(dest), ""
        shutil.move(str(src), str(dest))
        return str(dest), ""
    except Exception:
        logger.warning(
            "export relocate to %r failed -- keeping the original path",
            target_dir,
            exc_info=True,
        )
        return path, (
            "\n> ℹ️  I could not move the file into the folder you asked "
            "for, so it stayed at the path above."
        )


async def _export_suite_reply(
    suite, suite_id: str, fmt: str, output_dir: str, progress: _common.ProgressCb
) -> str:
    """Write the loaded suite in *fmt*, relocate it if asked, shape the reply."""
    await _common._emit(progress, f"📦 Exporting suite to {fmt}…")
    # I4 (2026-08-10): UNTRUSTED folder text, validated by exactly the same
    # rules as an elicited answer -- absolute / ~-rooted only, no config
    # lines, length-capped, root-allowlisted, with the "did you mean
    # ~/Desktop?" correction for a bare well-known word. Every branch below
    # keys off `target_dir`, the RESOLVED value: a rejected answer is
    # truthy as raw text but resolves to "", and must not send the file
    # anywhere. A rejection keeps the default destination AND says so.
    target_dir, dir_note = "", ""
    if (output_dir or "").strip():
        target_dir, _why = _safe_elicited_dir(
            output_dir, fallback_label=_TEMP_EXPORT_LABEL
        )
        if not target_dir:
            dir_note = _why
    try:
        path = await asyncio.to_thread(_available_exporters()[fmt], suite)
    except Exception as exc:
        logger.exception("mcp export failed")
        return f"⚠️ Export to {fmt} failed: {exc}"
    if target_dir:
        # Every surviving format writes ONE file, so relocating it is
        # safe. The exception was the Zephyr PAIR, which wrote itself
        # into target_dir and would have been split from its
        # zfj_import_config.json by a move; that exporter was DELETED
        # on 2026-08-15 (dead-code deletion batch D4).
        path, _move_note = _relocate_export(path, target_dir)
        if _move_note:
            dir_note += _move_note
    telemetry.add_tool_properties(format=fmt, case_count=len(suite.test_cases))
    await _common._audit(
        "mcp_export_suite",
        entity_id=suite_id,
        detail={"format": fmt, "path": path, "custom_dir": bool(target_dir)},
    )
    result = _reply.shape_export_result(suite_id, fmt, path, len(suite.test_cases))
    if dir_note:
        result += dir_note
    return result


async def _export_suite_resolve(
    suite_id: str, fmt: str, choose: _common.ChooseCb
) -> tuple[str, str, str | None]:
    """Settle the format and suite id; the third item is a reply that ends the call."""
    fmt = (fmt or "").strip().lower()
    if not fmt and _common._elicit_enabled():
        picked = await _elicit._elicit_choice(
            choose, "Which export format?", list(sorted(_available_exporters()))
        )
        if picked.status == _common.CHOSEN:
            fmt = (picked.value or "").strip().lower()
        elif picked.status == _common.DECLINED:
            return fmt, suite_id, "👍 Cancelled — no export format selected."
        else:
            return fmt, suite_id, _format_menu_markdown()
    if fmt not in _available_exporters():
        return (
            fmt,
            suite_id,
            f"⚠️ Unknown format '{_reply._clip_echo(fmt, 60)}'. Choose one of: "
            f"{', '.join(sorted(_available_exporters()))}.",
        )
    suite_id = (suite_id or "").strip()
    if not suite_id and _common._elicit_enabled():
        picked = await _elicit._elicit_suite(choose)
        if picked.status == _common.CHOSEN:
            suite_id = (picked.value or "").strip()
        elif picked.status == _common.DECLINED:
            return fmt, suite_id, "👍 Cancelled — no suite selected."
        else:
            return (
                fmt,
                suite_id,
                await _elicit._recent_suites_markdown("qa_export_suite"),
            )
    if not suite_id:
        return fmt, suite_id, await _elicit._recent_suites_markdown("qa_export_suite")
    return fmt, suite_id, None


def _export_prep_id_reply(safe_prep_id: str) -> str:
    """The next-call answer when a prep id arrives where a suite id belongs.

    2026-08-12: the two are easy to confuse right after a volume refusal -- the
    point at which a host is most likely to give up on the tool path -- so this
    answers with the next call rather than an error. One sanitiser, not two:
    the inline version this replaced dropped newlines instead of replacing
    them, concatenating the fragments either side.
    """
    return (
        f"⚠️ `prep_id` is not a suite id, so nothing was exported "
        f"and prep `{safe_prep_id}` is intact.\n\n"
        "A prep becomes exportable only once `qa_submit_suite` finalizes it: "
        "that call returns the `suite_id` this tool needs, and it already "
        "auto-exports the .xlsx for you.\n\n"
        "- Still short of the volume floor? Top up the short categories with "
        "`qa_submit_category`, then " + _finalize_route_phrase() + ".\n"
        "- `qa_prep_status` shows what is staged for this prep.\n\n"
        "Do NOT write the suite to a file yourself -- see the refusal note "
        "on `qa_submit_suite`."
    )


async def handle_export_suite(
    suite_id: str,
    fmt: str,
    *,
    output_dir: str = "",
    prep_id: str = "",
    choose: _common.ChooseCb = None,
    progress: _common.ProgressCb = None,
) -> str:
    """Export a stored suite to one format and return the written path.

    *output_dir* (I4, 2026-08-10) is the OPTIONAL, tester-supplied save folder.
    It exists because the auto-export save-location dialog is unreachable
    whenever QA_EXPORT_DIR resolves -- which it does on every default install --
    so tester control belongs on the call that is explicitly about exporting,
    as a parameter rather than an elicitation. Always optional: a required
    field here would break every existing caller. Empty keeps each format's
    current destination exactly -- a secure temp folder for all five
    single-file exporters, which since batch D4 deleted the Zephyr pair
    (2026-08-15) is every format there is. Never raises.
    """
    # A prep_id is not a suite id (see `_export_prep_id_reply`); suite_id wins
    # when both arrive. The id is host-supplied: ONE sanitiser caps it and
    # strips backticks/newlines so it cannot break out of its code span.
    _prep_only = host_submission_safe_prep_id(prep_id)
    if _prep_only and not (suite_id or "").strip():
        return _export_prep_id_reply(_prep_only)
    fmt, suite_id, early = await _export_suite_resolve(suite_id, fmt, choose)
    if early is not None:
        return early
    try:
        loaded = await load_suite(suite_id)
        if loaded.get("error"):
            return (
                f"⚠️ Could not load suite `{_reply._clip_echo(suite_id, 80)}`: "
                + _reply._clip_echo(loaded["error"], 300)
            )
        suite = loaded.get("content")
        if suite is None:
            return _reply._suite_unreadable_reply(suite_id, loaded)
        return await _export_suite_reply(suite, suite_id, fmt, output_dir, progress)
    except Exception as exc:
        logger.exception("handle_export_suite failed")
        _common._capture_error(exc, "qa_export_suite")
        return f"⚠️ Export failed: {exc}"
