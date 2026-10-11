"""Moved from handlers (refactor P2): prep_status."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, NamedTuple

from agents.host_category_jobs import (
    build_category_job,
    build_category_jobs_batch,
    normalize_category,
    prep_status_view,
)
from agents.host_image import image_relevance_counts, off_topic_images
from agents.host_orchestration import expected_category_names
from agents.host_prep_serde import PrepSerdeError, deserialize_prepared
from agents.host_submission import safe_prep_id
from handlers import _common, _reply, export
from tools import prep_store

logger = logging.getLogger("tools.mcp_handlers")

# --------------------------------------------------------------------------- #
# Host-mode ("boomerang") SUBMIT handlers -- ops-3d-1b
#
# The BACK half of host-mode generation. handle_submit_suite validates the
# host-generated suite, runs the SHARED _finalize_generation, and returns EITHER
# a one-round gap report (regenerate + resubmit with the same prep_id) OR the
# finished, persisted, exported suite. handle_submit_category records one
# category at a time for a weaker host. Like the PREPARE half this is DEAD CODE
# behind QA_GENERATION_MODE until ops-3d-3 wires routing -- nothing here is
# reachable from a server-mode path, and host-submitted JSON is treated as
# UNTRUSTED throughout (json.loads only, size-capped, and sanitized via
# tools/cell_sanitizer.sanitize_cell by EVERY exporter reachable from this path
# -- xlsx_generator, csv_exporter and testrail_exporter --
# before it reaches a spreadsheet cell).
# --------------------------------------------------------------------------- #


def _rtm_trace_detail(suite) -> dict:
    """The Step 0 traceability counts, flattened for an audit detail dict.

    Empty when the suite carries none (no ACs, or an older suite), so a run
    without traceability data produces a byte-identical audit row. Never
    raises -- the audit trail must not be able to break a generation.
    """
    try:
        trace = getattr(suite, "_rtm_trace", None)
        if not isinstance(trace, dict) or not trace.get("acs"):
            return {}
        return {
            "rtm_acs": int(trace.get("acs", 0)),
            "rtm_covered": int(trace.get("covered", 0)),
            "rtm_orphan_cases": int(trace.get("orphan_cases", 0)),
        }
    except Exception:
        logger.debug("could not read _rtm_trace", exc_info=True)
        return {}


# Batch C item 1 (2026-08-09): a live 97-case suite carried
# rtm_orphan_cases: 13 -- thirteen cases mapping to no acceptance criterion --
# and was accepted in silence, because the count only ever reached the audit DB
# (_rtm_trace_detail above). These render it as a NOTE. Never a refusal: extra
# cases beyond the ACs are legitimate and common, so the failure being fixed is
# the SILENCE, not the orphans.
_RTM_ORPHAN_MAX_NAMED = 5
_RTM_ORPHAN_TITLE_CAP = 80
_RTM_ORPHAN_ID_CAP = 16
# tc_ids reach this note from a HOST-authored suite, so they are untrusted text
# in exactly the way titles are (review M3). `re` is deliberately not imported by
# this module, hence a set -- the same shape as _IMAGE_NAME_ALLOWED above.
_CASE_ID_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _safe_case_id(raw: object) -> str:
    """Sanitize ONE tc_id before a note interpolates it into a backtick span.

    Allowlist + cap, because the note writes the id inside backticks and a
    backtick or a newline in a host-authored id would break out of the span.
    Never raises."""
    try:
        return "".join(ch for ch in str(raw or "") if ch in _CASE_ID_ALLOWED)[
            :_RTM_ORPHAN_ID_CAP
        ]
    except Exception:  # pragma: no cover - a sanitizer must never raise
        return ""


def _safe_case_title(raw: object) -> str:
    """Sanitize ONE case title before a note interpolates it.

    Titles are HOST-authored on this path, and the note puts them inside a
    backtick-and-quote span, so backticks (span breakout) and newlines (list
    breakout) are stripped and the length is capped -- the same discipline as
    agents/host_dedup._shortlist_safe, kept local so this module does not reach
    into another module's private helper. Never raises."""
    try:
        return (str(raw or "").replace("`", "").replace("\n", " ").strip())[
            :_RTM_ORPHAN_TITLE_CAP
        ]
    except Exception:  # pragma: no cover - a sanitizer must never raise
        return ""


def _rtm_orphan_note(suite: object) -> str:
    """Submit-time disclosure of cases that trace to NO acceptance criterion.

    "" whenever the suite carries no traceability data, no ACs, or no orphans --
    so a run without the data produces a byte-identical reply. Names the count
    out of the total plus the FIRST _RTM_ORPHAN_MAX_NAMED ids (with sanitized
    titles where they resolve) and a (+N more) marker, and asks for either a
    mapping or a confirmation that they are deliberate additions.

    NOTHING is dropped, rewritten or refused. Never raises -- a disclosure must
    not be able to break a finalize."""
    try:
        trace = getattr(suite, "_rtm_trace", None)
        if not isinstance(trace, dict) or not trace.get("acs"):
            return ""
        try:
            orphans = int(trace.get("orphan_cases", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return ""
        if orphans <= 0:
            return ""
        cases = list(getattr(suite, "test_cases", None) or [])
        total = len(cases)
        named, more = _rtm_orphan_named(suite, cases, orphans)
        return (
            "> \u2139\ufe0f  **Requirement mapping: "
            f"{orphans} of {total} case(s) trace to NO acceptance criterion.**"
            + (f" First: {named}{more}." if named else "")
            + " Either set each one's `requirement_id` to the AC it verifies, or "
            "confirm they are deliberate additions -- extra cases are perfectly "
            "legitimate, but an untraced case is invisible to the requirement "
            "coverage report, so nobody can tell the two apart. Nothing was "
            "changed or dropped.\n\n"
        )
    except Exception:
        logger.debug("_rtm_orphan_note failed", exc_info=True)
        return ""


def _rtm_orphan_named(suite: object, cases: list, orphans: int) -> tuple[str, str]:
    """(the named first orphans, the "(+N more)" marker) for the orphan note."""
    ids = [
        i
        for i in (
            _safe_case_id(x) for x in (getattr(suite, "_rtm_orphan_ids", None) or [])
        )
        if i
    ][:_RTM_ORPHAN_MAX_NAMED]
    titles: dict = {}
    for tc in cases:
        try:
            titles[_safe_case_id(getattr(tc, "tc_id", ""))] = _safe_case_title(
                getattr(tc, "title", "")
            )
        except Exception:  # pragma: no cover - defensive
            continue
    named = ", ".join(
        (f'`{i}` "{titles[i]}"' if titles.get(i) else f"`{i}`") for i in ids
    )
    more = f" (+{orphans - len(ids)} more)" if orphans > len(ids) else ""
    return named, more


def _no_coverage_signal_note(view: object) -> str:
    """Disclosure for a finalize that computed NO coverage signal at all.

    Batch C item 4 (2026-08-09): a real finalize logged
    "coverage tier=none | quality flags=no". The server-side coverage critic is
    a model call this chat-only path deliberately does not make
    (agents/test_scenario_agent, advisory_gaps=False), which is CORRECT -- but it
    left an 8-case suite able to pass with zero quality signal AND zero
    disclosure. This does not run the critic; it says that none ran.

    SILENT only when a caller passes a non-None ``view``. handle_submit_suite
    builds no coverage view, so on the submit path it always fires.

    ALWAYS-ON IS THE INTENT, not an oversight (review W2). A disclosure that
    fired only on small suites would be worse than none: its ABSENCE would
    then read as "coverage WAS checked", which is exactly the inference this
    batch exists to stop.

    2026-08-14 (batch 8b-ii): QA_ATOMIC_CHECKLIST_ENABLED was DELETED and the
    checklist hardcoded ON, which INVERTS how often this fires. No requirement
    matching runs on this server, so it is the standing truth about every
    suite.
    Never raises."""
    try:
        if view is not None:
            return ""
        return (
            "> \u2139\ufe0f  **No automated coverage critique ran on this "
            "suite.** The server-side critic is a model call this chat-only path "
            "does not make, and no requirement matching runs on this server "
            "either -- so the generation-volume floor is the ONLY "
            "quantitative gate this suite passed. Read the cases against the "
            "requirements yourself before signing them off.\n\n"
        )
    except Exception:  # pragma: no cover - a disclosure must never raise
        logger.debug("_no_coverage_signal_note failed", exc_info=True)
        return ""


def _dropped_note(parsed) -> str:
    """Non-silent disclosure of cases dropped as malformed during parse/salvage.
    Surfaced UNCONDITIONALLY on BOTH the gap reply and the finished reply -- the
    ops-3c review finding was that dropped_count must never be swallowed at this
    layer. dropped_reasons is already capped upstream (parse_host_suite)."""
    n = getattr(parsed, "dropped_count", 0) or 0
    salvaged = getattr(parsed, "salvaged_reasons", None) or []
    if not n and not salvaged:
        return ""
    lines: list[str] = []
    if n:
        reasons = getattr(parsed, "dropped_reasons", None) or []
        lines.append(
            f"> ⚠️  {n} submitted case(s) were dropped as malformed and are "
            "not included:"
        )
        lines += [f">   - {r}" for r in reasons]
    if salvaged:
        # 2026-08-31 (F9): these cases ARE in the suite -- only their optional
        # test-data plan was rejected. Reporting them under the "dropped" line
        # would be worse than silence: it would say a case is missing when it
        # is not.
        if lines:
            lines.append(">")
        lines.append(
            f"> ℹ️  {len(salvaged)} case(s) were KEPT with their optional "
            "`test_data` plan removed, because that plan failed validation. "
            "The steps and expected results are unchanged:"
        )
        lines += [f">   - {r}" for r in salvaged]
    return "\n".join(lines) + "\n\n"


def _prep_store_error_reply(prep_id: str, result: object) -> str:
    """Reply for a prep-store READ that FAILED, or "" when it did not.

    F6 (2026-09-02 audit): ``prep_store`` returns
    ``{"error": ..., "content": None}`` on a store failure, and every reader
    inspected ``content`` alone. So a corrupt database ("file is not a
    database"), a permissions problem or an unreadable file was reported to the
    tester as _prep_missing_reply's "No active preparation ... it is unknown,
    expired, or was already finalized. **Do NOT resubmit this prep_id**" --
    advice that abandons work still on disk, for a condition that is RETRYABLE.
    Never raises.
    """
    try:
        err = str((result or {}).get("error") or "").strip()
    except Exception:  # pragma: no cover - defensive
        return ""
    if not err:
        return ""
    return (
        "⚠️ Could not READ the preparation store for prep_id "
        f"`{_reply._clip_echo(prep_id, 80)}` -- this is a STORAGE failure, not an "
        "expired prep: " + _reply._clip_echo(err, 300) + "\n\n"
        "**Your staged work has NOT been lost and nothing needs regenerating "
        "yet.** Retry the same call once. If it fails again, run `qa-doctor` -- "
        "it reports where the store lives and whether this server can read it."
    )


def _prep_missing_reply(prep_id: str) -> str:
    """One message covering unknown / TTL-expired / already-finalized prep_ids.
    A finished suite deletes its prep, so a resubmit-after-finalize lands here
    too -- it must report cleanly, never crash or silently re-run.

    2026-08-03 (Fix 5a): the advice used to be a bare "Start again with
    `qa_prepare_test_cases`". Because a FINISHED suite is the most common way to
    reach here, that told a host whose suite had just been exported to launch a
    SECOND full generation of it. Observed on run3 (TICKET-5645): the suite
    finalized at 14:15:52 and the host kept rebuilding and resubmitting one
    category's payload until 14:18, three tool calls of which were rejected here.
    Re-preparing is only correct when the work was genuinely LOST, so the reply
    now separates the two cases and makes the finished case a stop, not a retry.

    NOTE this deliberately does not distinguish the three causes -- doing that
    needs the prep record to survive finalize (stamped rather than deleted), which
    is a prep-lifecycle change with four other readers to satisfy. Tracked
    separately; this message is correct for all three causes as written.
    """
    return (
        f"⚠️ No active preparation for prep_id "
        f"`{_reply._clip_echo(prep_id, 80)}`. It is unknown, "
        "expired (its TTL elapsed), or was already finalized (a finished suite "
        "deletes its prep).\n\n"
        "**Do NOT resubmit this prep_id, and do not re-prepare reflexively.** "
        "Check first:\n"
        "- **Did this run already finish?** If a suite summary and an export path "
        "came back for it, the work is DONE and saved -- stop here. Re-preparing "
        "would generate the whole suite a second time and cost every category "
        "again. Use `qa_export_suite` with that suite_id if you need another "
        "format.\n"
        "- **Only if the work was genuinely lost** (no suite was ever finalized, "
        "or the id is expired/unknown) start over with `qa_prepare_test_cases`."
    )


def _finalized_notice_lines(prep_id: str) -> list[str]:
    """The two opening lines of the already-finalized reply."""
    return [
        f"✅ Nothing to do: prep `{prep_id}` was ALREADY finalized "
        "successfully. Its cases are saved.",
        # B2 (2026-08-21, TICKET-5138): this reply is a NOTICE, not an artifact,
        # and it must say so before anything else. On the live run a second
        # editor process replayed a whole finalize against an
        # already-finalized prep and its host wrote all eight 520-byte
        # "nothing to do" replies over the files holding the REAL finalize
        # output, destroying it. The server cannot stop a host writing files;
        # the only lever it has is the CONTENT. The suite_id and export path
        # below are the recovery half -- they make an overwrite re-derivable.
        "\n- **this reply contains NO new output.** It is a no-op notice. "
        "Do "
        "NOT save it over, or replace, any file you wrote from the earlier "
        "successful finalize -- that file is the real result.",
    ]


def _finalized_reply(prep_id: str, record: object) -> str:
    """Reply for a prep whose suite ALREADY finalized successfully.

    Fix 5b (2026-08-03). Before this, finalize deleted the prep, so a resubmit hit
    _prep_missing_reply, which could not distinguish finished work from a lost or
    unknown id. On the observed run (TICKET-5645) the suite finalized at 14:15:52 and
    the host went on rebuilding and resubmitting a category's payload until 14:18.
    Naming the finished suite -- and its export path -- makes "stop" actionable
    instead of leaving "do not re-prepare" as advice the host has to trust.

    Returns "" when the record is not a finalized prep, so callers can use it as a
    guard exactly like _host_task_reply. Never raises.
    """
    try:
        if not prep_store.is_finalized_record(record):
            return ""
        info = record.get(prep_store.FINALIZED_KEY) or {}
        suite_id = str(info.get("suite_id") or "").strip()
        export_path = str(info.get("export_path") or "").strip()
        lines = _finalized_notice_lines(prep_id)
        if suite_id:
            lines.append(f"- **suite_id:** `{suite_id}`")
        if export_path:
            lines.append(f"- **exported to:** `{export_path}`")
        lines.append(
            "\n**Do NOT resubmit and do NOT re-prepare** -- re-preparing would "
            "generate the whole suite a second time and cost every category again. "
            + (
                f"For another format use `qa_export_suite` with suite_id `{suite_id}`."
                if suite_id
                else "Use `qa_export_suite` if you need another format."
            )
        )
        return "\n".join(lines)
    except Exception:
        logger.exception("_finalized_reply failed")
        return ""


def _host_task_reply(prep_id: str, record: object) -> str:
    """Reply naming the id KIND when a loaded prep record is a host_llm task.

    ``tools/host_llm.py`` task records share the preps table (one id space) with
    generation preps, so a host-task id pasted into a test-case tool would
    otherwise reach ``deserialize_prepared`` and come back as a misleading
    "corrupted or from an incompatible version" error -- or, in
    ``handle_prep_status``, as a misleading "staged 0/0, ready: no". One helper,
    used at ALL FOUR load sites that rehydrate a generation prep
    (``qa_get_category_job``, ``qa_submit_category``, ``qa_submit_suite`` and
    ``qa_prep_status``) so they cannot drift apart. Returns "" when the record is
    not a host task. Never raises.
    """
    try:
        from tools import host_llm as _host_llm

        if not _host_llm.is_host_task_record(record):
            return ""
    except Exception:  # pragma: no cover - a hint must never break a tool call
        return ""
    return (
        f"⚠️ `{prep_id}` is a host-task id (opened by `tools/host_llm.py`), not a "
        "test-case prep id — nothing is corrupted. Submit it with the submit tool "
        "named in that task's envelope, or run `qa_prepare_test_cases` to start a "
        "test-case flow."
    )


async def _load_open_prep(
    prep_id: str, *, require_dict: bool = False, wrap_final=None
) -> tuple[object, str]:
    """Load a generation prep and run the refusals every prep tool shares.

    Returns ``(envelope, "")`` when the prep can be used, ``(None, reply)`` when
    one of the four refuses. The order is fixed and the same at all four load
    sites (``qa_prep_status``, ``qa_get_category_job``, ``qa_submit_category``,
    ``qa_submit_suite``): a host-task id, a store READ failure, no prep, a prep
    that already finalized. A new refusal belongs HERE, so no tool can act on a
    prep the others refuse.

    ``require_dict`` reports any non-dict envelope as a missing prep (only
    ``qa_submit_suite`` asks for that); ``wrap_final(prep_id, note)`` reshapes
    the finalized note (``qa_prep_status`` gives it its terminal status shape).
    Raises whatever ``prep_store.load_prep`` raises: callers keep their own
    ``try``.
    """
    loaded = await prep_store.load_prep(prep_id)
    envelope = loaded.get("content")
    refusal = _host_task_reply(prep_id, envelope) or _prep_store_error_reply(
        prep_id, loaded
    )
    if refusal:
        return None, refusal
    missing = not isinstance(envelope, dict) if require_dict else envelope is None
    if missing:
        return None, _prep_missing_reply(prep_id)
    final_note = _finalized_reply(prep_id, envelope)
    if final_note:
        return None, wrap_final(prep_id, final_note) if wrap_final else final_note
    return envelope, ""


async def _unfinished_preps_note(exclude_prep_id: str = "") -> str:
    """Markdown disclosure of in-flight preps (fetched worker packets or
    staged category rows) so an interrupted host-mode run is resumable
    instead of evaporating at TTL (2026-07-31 TICKET-5645 incident).

    DISCLOSURE ONLY -- never blocks anything. Returns "" when nothing
    qualifies or the store errors. The line prints the prep_id, which is the
    capability token for that prep. Times render as the server's local HH:MM.
    Never raises."""
    try:
        res = await prep_store.list_unfinished_preps(limit=3)
        rows = res.get("content") or []
        lines = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            pid = str(r.get("prep_id") or "")
            if not pid or pid == exclude_prep_id:
                continue
            started = time.strftime(
                "%H:%M", time.localtime(float(r.get("created_at") or 0))
            )
            expires = time.strftime(
                "%H:%M", time.localtime(float(r.get("expires_at") or 0))
            )
            staged = int(r.get("staged_count") or 0)
            expected = int(r.get("expected_count") or 0)
            count = f"{staged}/{expected}" if expected else f"{staged}"
            lines.append(
                f"> ⏳ Unfinished prep `{pid}` from {started}: {count} "
                f"category row(s) staged, expires ~{expires}. Resume with "
                "`qa_prep_status` + `qa_submit_category` (same prep_id), "
                "or ignore it -- it expires on its own."
            )
        return ("\n".join(lines) + "\n") if lines else ""
    except Exception:
        logger.debug("unfinished-preps disclosure failed", exc_info=True)
        return ""


_SIDECAR_KEYS = ("duplicate_groups", "acceptance_criteria", "ambiguity_result")

# What a sidecar-only submit may leave on the prep for a later finalize (F3).
# A CLOSED set, deliberately wider than _SIDECAR_KEYS because the two
# meta-gated fields are copied by the same merge block: an unbounded copy would
# let a host park arbitrary keys in the envelope.
_SIDECAR_RETAINABLE = (
    "duplicate_groups",
    "acceptance_criteria",
    "ambiguity_result",
    "checklist_items",
    "image_descriptions",
)


def _sidecar_keys(meta: object = None) -> tuple:
    """The recognised sidecar review fields for THIS request.

    _SIDECAR_KEYS itself stays pinned by tests/test_host_ac_review.py.
    Never raises.

    Recognition is keyed off the PREP'S OWN meta stamp whenever ``meta`` is
    supplied -- never off live flag values, which is how the copy logic in
    handle_submit_suite already works, and which is what stops a flag flipped
    between prepare and submit from turning a legitimate sidecar into a
    confusing full-suite parse error. With no meta (module-level and legacy
    callers) the checklist falls back to the checklist_enabled() seam -- read
    rather than inlined so every checklist surface answers to the one switch
    tests/conftest.py pins.

    Phase 3a's two post_merge fold fields (`risk_scores` / `test_plan_report`)
    left this function on 2026-08-16 with the jobs themselves (dead-code
    deletion P2-H)."""
    try:
        keys = _SIDECAR_KEYS
        # The image job's return field is finalize-time review material like
        # the others, so the staged (crash-safe) route must be able to carry
        # it in a sidecar.
        keys = keys + ("image_descriptions",)
        if isinstance(meta, dict):
            pass
        # Residue R4: the checklist job's return field is finalize-time material
        # like the others, so the staged (crash-safe) Path A route must be able
        # to carry it in a sidecar. Recognised from the prep's OWN meta stamp
        # when meta is supplied (the Phase-3a pattern), with the live AND as the
        # no-meta fallback. Deliberately NO id remap: CL-NNN ids are assigned
        # server-side in extract_host_checklist and are never tc_ids, so 3a's
        # _remap_risk_scores problem -- every staged category restarting at
        # TC-001 -- cannot arise here.
        from tools.atomic_checklist import checklist_enabled

        if (
            bool(meta.get("host_checklist_job"))
            if isinstance(meta, dict)
            else checklist_enabled()
        ):
            keys = keys + ("checklist_items",)
        return keys
    except Exception:  # pragma: no cover
        logger.debug("_sidecar_keys flag read failed", exc_info=True)
    return _SIDECAR_KEYS


def _retained_sidecar(meta: object) -> dict:
    """The review sidecar an EARLIER sidecar-only submit left on this prep.

    Empty dict when there is none, when the envelope predates the retention
    LIFETIME -- the retention is never cleared, on purpose. Reviewed 2026-08-29:
    a second finalize that OMITS a key would re-apply the value the first one
    retained, and the present-but-empty discipline below covers "sent empty" but
    not "not sent at all". That is unreachable rather than merely unlikely: a
    successful finalize STAMPS the prep (``FINALIZED_KEY``, see the update_prep
    near the end of handle_submit_suite) rather than deleting it, and the next
    submit returns at the already-finalized guard before any sidecar work runs.
    The only route that finalizes twice on one prep is gap remediation, which is
    itself unreachable while ``checklist_remediation_enabled()`` is a ``False``
    constant -- and on THAT route re-applying is the behaviour you want: same
    prep, same ticket, so step-0 work should survive a remediation round rather
    than be silently dropped from the second suite. Clearing on fold would turn
    this into exactly the loss the retention exists to prevent.

    (an old prep simply has no `pending_sidecar` key) or when the stored value
    is not a dict. UNTRUSTED on the way back in: it is host-authored JSON that
    happens to have been round-tripped through our own store, so it is handed
    back into exactly the same `_apply`/`parse_host_suite` path a fresh
    submission takes and is re-validated there. Never raises.
    """
    try:
        pending = (meta or {}).get("pending_sidecar")  # type: ignore[union-attr]
        return dict(pending) if isinstance(pending, dict) else {}
    except Exception:
        logger.debug("_retained_sidecar read failed", exc_info=True)
        return {}


def _merge_retained_sidecar(sidecar_obj, meta: object):
    """This call's sidecar laid over the retained one (either may be absent).

    Returning a non-None value here is what lets a BARE finalize -- no
    suite_json, no sidecar -- still reach the sidecar merge branch and pick up
    the acceptance criteria, checklist and ambiguity verdict a previous
    sidecar-only submit produced. That is F3's whole acceptance path.

    PRESENT-BUT-EMPTY DISCIPLINE is preserved end to end: only keys a sidecar
    actually MENTIONED are stored and only those are overlaid, so a field
    neither sidecar named stays absent and can never read downstream as "the
    host reviewed and found none".
    """
    try:
        retained = _retained_sidecar(meta)
        if not retained:
            return sidecar_obj
        if not isinstance(sidecar_obj, dict):
            return retained
        merged = dict(retained)
        merged.update(sidecar_obj)
        return merged
    except Exception:
        logger.debug("_merge_retained_sidecar failed", exc_info=True)
        return sidecar_obj


def _superseded_sidecar_note(sidecar_obj, meta: object) -> str:
    """Name the retained keys this call's sidecar overwrote.

    A silent overwrite is the same class of loss F3 exists to close: the host
    would have no way to know its earlier review had been replaced.
    """
    try:
        retained = _retained_sidecar(meta)
        if not retained or not isinstance(sidecar_obj, dict):
            return ""
        clashes = sorted(k for k in sidecar_obj if k in retained)
        if not clashes:
            return ""
        return (
            "> \u2139\ufe0f  This submission's `"
            + "` / `".join(clashes)
            + "` REPLACED the value retained from an earlier sidecar-only "
            "submit on this prep.\n\n"
        )
    except Exception:
        logger.debug("_superseded_sidecar_note failed", exc_info=True)
        return ""


def _with_retained_sidecar(suite_json, meta: object):
    """Fold a retained sidecar into a FULL (Path B) submission.

    CRITICAL CONTRACT: the submitted `test_cases` are what must never be lost,
    so every doubt here returns `suite_json` UNCHANGED. A retained field is
    added ONLY when the submission does not carry that key itself -- this call's
    own values always win, and a key neither side mentioned stays absent, which
    is the same present-but-empty discipline the sidecar merge uses.

    Path B needs its own fold because `_review_sidecar` returns None for a
    submission that HAS test cases, so a full suite can never enter the sidecar
    merge branch -- and must not, since that branch rebuilds the suite from
    staged rows and would discard everything the host just sent.
    """
    try:
        retained = _retained_sidecar(meta)
        if not retained:
            return suite_json
        if isinstance(suite_json, str):
            data = json.loads(suite_json)
        elif isinstance(suite_json, dict):
            data = suite_json
        else:
            return suite_json
        if not isinstance(data, dict) or not data.get("test_cases"):
            return suite_json
        merged = dict(data)
        for key, value in retained.items():
            if key not in merged:
                merged[key] = value
        return merged
    except Exception:
        logger.debug(
            "_with_retained_sidecar failed - using the submission as sent",
            exc_info=True,
        )
        return suite_json


def _review_sidecar(suite_json, meta: object = None) -> "dict | None":
    """Return the parsed object when suite_json is a REVIEW SIDECAR.

    A sidecar is a JSON object that carries at least one of the recognised
    post-merge review fields (_SIDECAR_KEYS) and has no test cases (missing
    or empty ``test_cases``). It is how the per-category route (Path A) can
    still deliver fields that _merge_category_rows structurally drops --
    generalised from the duplicate-groups-only sidecar so the AC boomerang
    works on that route too. Never raises.
    """
    try:
        if isinstance(suite_json, str):
            raw = suite_json.strip()
            if not raw:
                return None
            data = json.loads(raw)
        elif isinstance(suite_json, dict):
            data = suite_json
        else:
            return None
        if not isinstance(data, dict):
            return None
        if not any(k in data for k in _sidecar_keys(meta)):
            return None
        cases = data.get("test_cases")
        if cases is None or cases == []:
            return data
        return None
    except Exception:
        logger.debug("review sidecar parse failed", exc_info=True)
        return None


# _dedup_sidecar_groups (below) is superseded by _review_sidecar for the
# ONE call site that used to invoke it (handle_submit_suite), which now calls
# _review_sidecar instead. Retained -- not deleted -- solely because the
# evening ops' own tests still call it directly; it is dead in the actual
# request path.
def _dedup_sidecar_groups(suite_json) -> "object | None":
    """Return raw duplicate_groups if suite_json is a dedup-only sidecar.

    A sidecar is a JSON object that carries ``duplicate_groups`` and has no
    test cases (missing or empty ``test_cases``). Never raises.
    """
    try:
        if isinstance(suite_json, str):
            raw = suite_json.strip()
            if not raw:
                return None
            data = json.loads(raw)
        elif isinstance(suite_json, dict):
            data = suite_json
        else:
            return None
        if not isinstance(data, dict) or "duplicate_groups" not in data:
            return None
        cases = data.get("test_cases")
        if cases is None or cases == []:
            return data.get("duplicate_groups")
        return None
    except Exception:
        logger.debug("dedup sidecar parse failed", exc_info=True)
        return None


def _remap_dup_groups(groups, id_map: dict) -> list:
    """Remap tc_ids in duplicate_groups through a merge id_map. Never raises."""
    out = []
    try:
        for g in groups or []:
            if not isinstance(g, (list, tuple)):
                continue
            mapped = [id_map.get(tid, tid) for tid in g if isinstance(tid, str)]
            if mapped:
                out.append(mapped)
    except Exception:
        logger.debug("dup group remap failed", exc_info=True)
        return list(groups or [])
    return out


def _fanout_incomplete_note(meta: object, rows: list, prep_id: str) -> str:
    """Refusal text when a parallel-fan-out prep's staged set is INCOMPLETE.

    Returns "" when this prep never requested fan-out, or every expected
    category is staged. ONE decision point shared by BOTH staged finalize
    routes -- the bare ``suite_json=""`` merge AND the review-SIDECAR merge --
    because a sidecar finalize builds exactly the same merged suite from the
    same rows and deletes the prep on success. The sidecar branch used to be
    ungated (it only checked "are there any rows at all"), so a host that
    crashed after staging 5 of 8 categories and then followed the "finalize
    with a review sidecar" instruction shipped a silently truncated 5/8 suite
    -- precisely the loss this gate exists to prevent. Never raises; on an
    unexpected error it fails OPEN (returns "") so a legitimate finalize is
    never blocked by the guard itself.
    """
    try:
        if not isinstance(meta, dict) or not meta.get("parallel_fanout"):
            return ""
        expected = list(meta.get("expected_categories") or [])
        staged_names = [
            str(r.get("category_name") or "") for r in rows if isinstance(r, dict)
        ]
        status = prep_status_view(expected=expected, staged_raw_names=staged_names)
        if status.get("ready"):
            return ""
        missing = (
            ", ".join(f"`{m}`" for m in (status.get("missing") or [])) or "(unknown)"
        )
        return (
            # D3 (2026-08-21): this refusal used to name the retired fan-out
            # workflow, which this server no longer asks for anywhere (the old
            # wording is deliberately not quoted back here -- see the batch
            # reply above). The GATE is unchanged: it is the crash-safety half
            # of why the staged route was kept.
            "⚠️ Incomplete staged submission: not every expected "
            f"category is staged for prep_id `{prep_id}`.\n\n"
            f"Staged {status.get('staged_count', 0)}/"
            f"{status.get('expected_count', 0)}. Missing: {missing}.\n\n"
            "Call `qa_submit_category` for each missing category "
            "(or submit a full merged suite_json), then finalize "
            "again. `qa_prep_status` shows the current set."
        )
    except Exception:  # pragma: no cover - defensive, must never block a finalize
        logger.debug("fan-out completeness gate failed", exc_info=True)
        return ""


# Batch 1 (2026-08-09), thresholds revised after review round 1.
#
# _VOLUME_REFUSE_RATIO -- the share of the prep's OWN summed per-category floor
# below which a finalize is REFUSED rather than warned about.
# _VOLUME_WARN_SLACK is GONE (E02, 2026-08-20). It allowed ONE category to sit
# exactly one case under the floor before being reported -- measured against the
# two known-good runs of 2026-08-04 (suites 26600607... = 99 cases and
# 4ecf093a... = 97 cases, band-2, floor 12), which bottom out at EXACTLY 12 per
# category. It is not relaxed but SUBSUMED: with redistribution legitimate, a
# category is reported only when it is empty or below HALF its floor, and every
# 11/12 dip that the slack existed to silence is far inside that line. Leaving
# it in place would have been a second, tighter threshold that could never fire.
# An EMPTY category was never slack and still is not.
# _VOLUME_MAX_NAMED -- how many short categories the note lists by name. All
# 8 canonical categories fit, so the 08-09 shape (every category short)
# names them all; the cap only bounds a longer, hand-built list.
#
# The asymmetry survives the slack's deletion and is now the whole design: a
# CATEGORY may come in under its floor, the TOTAL may not. At floor 12, two
# categories at 11 (94 of 96) still warn -- not because 11 is too few for a
# category, but because 94 is too few for the suite, which is the number the
# prep actually contracted for and the one the 2026-08-09 collapse violated.
#
# Module CONSTANTS, not settings fields: every decision here keys off the prep's
# stamps so a mid-flow .env flip cannot change an in-flight prep, and a ratio
# that lives in code can only move with a code update -- which
# _version_skew_note already discloses to the tester.
_VOLUME_REFUSE_RATIO = 0.5
_VOLUME_MAX_NAMED = 8


class _VolumeVerdict(NamedTuple):
    """The decision behind _volume_floor_note, with no wording in it."""

    mode: str
    floor: int
    names: list
    total: int
    unknown: int
    short: list
    empty: list
    refused_before: bool

    @property
    def floor_total(self) -> int:
        return self.floor * len(self.names)


def _volume_contract(meta: object) -> "tuple[int, list] | None":
    """This prep's ``(floor, category names)`` stamps, or None when it has none."""
    if not isinstance(meta, dict) or not meta.get("volume_floor"):
        return None
    try:
        floor = int(meta.get("volume_min_cases") or 0)
    except (TypeError, ValueError, OverflowError):
        floor = 0
    names = [
        str(n).strip() for n in (meta.get("volume_categories") or []) if str(n).strip()
    ]
    if floor <= 0 or not names:
        return None
    return floor, names


def _bucket_cases_by_category(cases: list) -> "tuple[dict, int, int]":
    """``(count per canonical category, unlabelled count, total)`` of ``cases``."""
    counts: dict = {}
    unknown = 0
    total = 0
    for tc in cases or []:
        total += 1
        canon = normalize_category(getattr(tc, "category", None))
        if canon:
            counts[canon] = counts.get(canon, 0) + 1
        else:
            unknown += 1
    return counts, unknown, total


def _volume_verdict(
    meta: object, cases: list, *, ack: bool = False, post_dedup: bool = False
) -> "_VolumeVerdict | None":
    """Is this suite too thin for THIS prep's own floor? None means silent.

    Pure: reads the prep's stamps and the cases, renders nothing. The why of
    every rule is on _volume_floor_note.
    """
    contract = _volume_contract(meta)
    if contract is None:
        return None
    floor, names = contract
    stamps: dict = meta  # type: ignore[assignment]  # a dict once it has a contract
    counts, unknown, total = _bucket_cases_by_category(cases)
    floor_total = floor * len(names)
    short = [(n, counts.get(n, 0)) for n in names if counts.get(n, 0) < floor]
    empty = [n for n, got in short if got == 0]
    # E02: below HALF its floor a category has not judged its material thin,
    # it has collapsed -- that is the 08-09 shape surviving inside one
    # category. Everything between that line and the floor is the
    # redistribution the prompt now asks for and is not reported.
    collapsed = [n for n, got in short if got < floor * _VOLUME_REFUSE_RATIO]
    # Clean exit: the summed floor is met AND no category is empty or
    # collapsed. Both are checked because unlabelled cases belong to no
    # category; once either opens the note, it names every short category.
    if total >= floor_total and not collapsed and not empty:
        return None
    # A materially-short TOTAL refuses outright. A per-category refusal is
    # restricted to a genuinely ABSENT category on a prep that asked for the
    # fan-out, and only when the unlabelled cases are too few to account for
    # it -- everything else is a warning.
    if total < floor_total * _VOLUME_REFUSE_RATIO:
        mode = "refuse"
    elif bool(stamps.get("parallel_fanout")) and empty and unknown < floor:
        mode = "refuse"
    else:
        mode = "warn"
    if post_dedup:
        # F5 (2026-08-15): warn, never stay silent -- the suite is already
        # final, and the tester must not lose it over cases the server removed.
        mode = "warn"
    refused_before = bool(stamps.get("volume_refused"))
    if mode == "refuse" and ack and refused_before:
        mode = "acked"
    return _VolumeVerdict(
        mode, floor, names, total, unknown, short, empty, refused_before
    )


def _volume_shown(short: list, floor: int) -> str:
    """The below-floor categories as ``name got/floor``, capped at a few."""
    shown = ", ".join(f"`{n}` {got}/{floor}" for n, got in short[:_VOLUME_MAX_NAMED])
    if len(short) > _VOLUME_MAX_NAMED:
        shown += f", (+{len(short) - _VOLUME_MAX_NAMED} more)"
    return shown


def _volume_shrink_fact(verdict: _VolumeVerdict, meta: dict, post_dedup: bool) -> str:
    """The "smaller than the refused one" line, or "" when it does not apply.

    Refusal channel ONLY. post_dedup measures the FINAL suite after the
    server's own de-duplication, so a smaller number there is the server's
    doing, not the host's, and the suite is already finalized -- the top-up
    advice would be wrong on both counts. On the `acked` beat the tester has
    just signed the smaller suite off, so nagging about it is equally wrong.
    """
    try:
        last = int(meta.get("volume_last_count") or 0)
    except (TypeError, ValueError, OverflowError):
        last = 0
    if last <= verdict.total or post_dedup or verdict.mode != "refuse":
        return ""
    return (
        f"- **⚠️ This submission is SMALLER than the one "
        f"refused a moment ago ({last} → {verdict.total} cases).** Cases "
        "are being removed, not added, and generating fewer is not a "
        "way through a volume refusal. Take one of the routes below."
    )


def _volume_facts(verdict: _VolumeVerdict, meta: dict, post_dedup: bool) -> list:
    """The bullet facts shared by the refusal and the warning."""
    v = verdict
    facts = [
        f"- **{'In the FINAL suite' if post_dedup else 'Submitted'}:** "
        f"{v.total} case(s), {v.total - v.unknown} of them carrying a recognised "
        "category.",
        f"- **This prep asked for:** {v.floor_total} case(s) overall "
        f"({v.floor} per category × {len(v.names)} categories). A category "
        "may come in under that number when it genuinely has less to test, "
        "PROVIDED the suite makes it up elsewhere -- what is reported here "
        "is the suite falling short overall, a category with nothing in it, "
        "or one below half its share.",
        f"- **Below that floor:** {_volume_shown(v.short, v.floor) or '(none)'}.",
    ]
    shrink = _volume_shrink_fact(v, meta, post_dedup)
    if shrink:
        facts.append(shrink)
    if v.unknown:
        facts.append(
            f"- **{v.unknown} case(s) carried no recognisable `category`**"
            + (
                " -- too few to account for an empty category"
                if v.empty and v.unknown < v.floor
                else ""
            )
            + ". They count toward the total but toward no category; set "
            "each case's `category` to one of the payload's own category "
            "names."
        )
    return facts


_VOLUME_OPT_ACK = (
    "Or, ONLY if the tester has seen these numbers and confirms "
    "a smaller suite is right for this feature, resubmit unchanged "
    "with `volume_floor_ack=true`. Ask them first -- do not decide "
    "that on your own judgement."
)


def _volume_rebuild_tail(verdict: _VolumeVerdict, prep_id: str) -> str:
    """The full-rebuild option after its lead-in, shared by both orderings."""
    return (
        "resubmit the COMPLETE suite with the SAME prep_id "
        f"`{prep_id}` -- the suite needs {verdict.floor_total} case(s) overall. "
        f"`categories[].min_cases` asks {verdict.floor} per category and that "
        "is the right target for each, but a category with genuinely "
        "less to test may come in under it PROVIDED another covers "
        "the difference; what may not stand is the total, an empty "
        "category, or one below half its share. The number came from "
        "THIS feature's own complexity, not a fixed floor."
    )


def _volume_staged_topup(prep_id: str, staged: int) -> str:
    """The top-up option when the server already HOLDS staged categories."""
    return (
        "**Top up per category -- this is the route here.** "
        f"{staged} categor"
        + ("y is" if staged == 1 else "ies are")
        + f" already staged on prep `{prep_id}`, so **do NOT resend "
        "the cases you already sent**. Call `qa_submit_category` "
        "again for the SHORT categories ONLY (a repeat call REPLACES "
        "that category's staged row, so send that category's full "
        f"set), then call `qa_submit_suite` with prep_id `{prep_id}` "
        "and "
        + export._finalize_route_phrase(short=True)
        + " -- the finalize rebuilds the suite "
        "from the staged rows. `qa_prep_status` shows the set. "
        "Regenerating the whole suite instead costs a second full "
        "pass AND silently changes cases the tester already reviewed."
    )


def _volume_options(verdict: _VolumeVerdict, prep_id: str, staged: int) -> str:
    """The numbered "Pick one" list of a refusal.

    ``staged`` reorders it: when the server already holds the cases the
    top-up route LEADS and the full rebuild is demoted to the fallback it is.
    """
    rebuild = _volume_rebuild_tail(verdict, prep_id)
    if staged:
        options = [
            _volume_staged_topup(prep_id, staged),
            "Or, only if you would rather rebuild every category from "
            "scratch: generate the missing cases and " + rebuild,
            _VOLUME_OPT_ACK,
        ]
    else:
        options = [
            "Generate the missing cases and " + rebuild,
            "Or top up per category: call `qa_submit_category` again for "
            "each short category (a repeat call REPLACES that category's "
            "staged row, so send its full set), then "
            + export._finalize_route_phrase()
            + ". `qa_prep_status` shows the set.",
            _VOLUME_OPT_ACK,
        ]
    return "".join(f"{i}. {opt}\n" for i, opt in enumerate(options, 1))


def _volume_refusal_text(
    verdict: _VolumeVerdict, facts: list, prep_id: str, *, ack: bool, staged: int
) -> str:
    """The markdown of a ``refuse`` verdict."""
    ignored_ack = ""
    if ack and not verdict.refused_before:
        ignored_ack = (
            "\n\n> ⚠️  `volume_floor_ack=true` arrived on the "
            "FIRST submit for this prep and was IGNORED: the tester "
            "cannot have seen these numbers yet. Show them the figures "
            "above; if they confirm the smaller suite is right, "
            "resubmit it unchanged with the ack and it WILL be "
            "honoured."
        )
    return (
        "⛔ **Submission refused:** this suite is materially below "
        "the generation volume this prep's own payload asked for.\n\n"
        + "\n".join(facts)
        + ignored_ack
        + f"\n\nNothing was discarded and prep `{prep_id}` is intact -- "
        "the prepared context and every staged category are still "
        "there.\n\n"
        "**Pick one:**\n" + _volume_options(verdict, prep_id, staged) + "\n"
        "Do **not** write the suite to a file yourself. A hand-authored "
        "CSV or XLSX skips de-duplication, the coverage critic, "
        "requirements traceability, risk scoring, the auto-export and "
        "the audit log, and it has no `suite_id` -- it is not a "
        "substitute for finalizing here, whatever the tester agreed to."
    )


def _volume_warning_head(verdict: _VolumeVerdict, post_dedup: bool) -> str:
    """The HEAD line of a warning, naming and quantifying the shortfall.

    F5: the old head said only "Volume below the requested floor" and the tail
    said the suite "was accepted as submitted", so the one number that matters
    (`Boundary Values` 2/8) sat in the middle of a blockquote and a host
    summarising the reply kept the .xlsx path and dropped this.
    """
    v = verdict
    worst = min(v.short, key=lambda pair: pair[1]) if v.short else None
    return (
        "> ⚠️  **UNDER-GENERATED"
        + (
            " (in the FINAL suite, after de-duplication and any re-filing)"
            if post_dedup
            else ""
        )
        + (
            " -- refusal OVERRIDDEN by `volume_floor_ack=true`"
            if v.mode == "acked"
            else ""
        )
        + ":** "
        + (
            f"{len(v.short)} of {len(v.names)} categories are below the number "
            "of cases this prep asked for"
            + (
                f", the worst being `{worst[0]}` with {worst[1]} of {v.floor}"
                if worst
                else ""
            )
            if v.short
            else f"the suite totals {v.total} case(s) against {v.floor_total}"
        )
        + ".\n"
    )


def _volume_warning_text(
    verdict: _VolumeVerdict, facts: list, prep_id: str, post_dedup: bool
) -> str:
    """The markdown of a ``warn`` or ``acked`` verdict."""
    return (
        _volume_warning_head(verdict, post_dedup)
        + "".join(f">   {line}\n" for line in facts)
        + ">   **This suite was still accepted and exported** -- nothing "
        "here blocked it, and the shortfall is recorded on the "
        '"Generation Notes" sheet of the workbook. Show the tester these '
        "numbers. If the short categories were not a deliberate choice, "
        "regenerate them and resubmit with prep_id "
        f"`{prep_id}`.\n\n"
    )


# WHY _volume_floor_note EXISTS: every ``categories[]`` entry and every job
# packet of the prepare payload carries ``min_cases``
# (host_mode.build_prepare_payload), and nothing on the submit side ever
# checked it. On 2026-08-09 08:23 a host ignored the fan-out contract, produced
# ONE case per category inline in the parent turn and finalized a merged 8-case
# suite 28 seconds after prepare; it was accepted in silence and exported as if
# normal, against 99 and 97 cases (never fewer than 12 per category) on two
# comparable runs.
#
# ONE decision point for BOTH halves of that failure -- the volume floor, and
# the fan-out completeness check that _fanout_incomplete_note only ever applied
# to the two STAGED routes ("Path B (non-empty suite_json) is unaffected", as
# its call site put it). A prep that stamped ``parallel_fanout`` and finalizes
# a merged blob missing a whole expected category never honoured the
# orchestration contract either.
#
# IT RUNS ON BOTH ROUTES. Path A -- ``qa_submit_category`` x N then an empty
# ``suite_json`` -- is what ``build_orchestration`` marks ``preferred``, so
# gating only the merged route would have left the recommended one as a free
# bypass: 8 staged rows of 1 case each pass _fanout_incomplete_note (complete,
# not sufficient) and ship the same 8-case suite. Bucketing is in fact MORE
# reliable there, because _merge_category_rows stamps each case's ``category``
# from the SERVER-DERIVED ``category_name`` of the tool call.
#
# R1: this binds a PARTIAL staged set even when the prep never requested the
# fan-out. handle_submit_category also serves "a weaker host that submits
# incrementally", and for such a prep _fanout_incomplete_note returns "" for
# ANY subset, so staging 3 of 8 categories used to finalize silently. The
# volume contract applies regardless of route: 3 of 8 is under-generation
# however the cases arrived. Only the EMPTY-category refusal is
# fan-out-specific, because only that prep was promised one worker per
# category.
#
# PER-CATEGORY DERIVATION ON PATH B: a merged case carries its OWN ``category``
# (``category_source: "host"``). This runs AFTER the has_full normalisation
# loop, so every value has already been through ``normalize_category``; it
# re-normalises defensively (idempotent) and buckets anything MISSING or
# UNRECOGNISED into ``unknown``. Unknown cases count toward the TOTAL (they are
# real cases) but toward no category, and they are NAMED in the note. They
# downgrade an empty-category refusal only when there are at least ``floor`` of
# them -- enough to plausibly BE the missing category. One deleted ``category``
# field must not disarm the gate, while a suite of 100 unlabelled cases must
# not be refused for a labelling defect; that is a PROPORTIONAL test, not an
# on/off one.
#
# Every decision reads this prep's META STAMPS (``volume_floor``,
# ``volume_min_cases``, ``volume_categories``, ``parallel_fanout``,
# ``volume_refused``), never a live settings flag, so neither a mid-flow .env
# flip nor a launcher auto-update between prepare and submit can change an
# in-flight prep. An envelope written before those stamps existed returns
# ``("", "")`` on the first guard -- inert, never a spurious refusal.
#
# E02 (2026-08-20) -- REDISTRIBUTION IS LEGITIMATE. The per-category floor used
# to be reported whenever a category came in under it, even when the suite as a
# whole honoured the contract. That was consistent with the prompt of the day,
# which asked every category for the same number and got it: 3/3 measured runs
# returned an identical count across all eight workers (12, 12, 13; zero
# within-run disagreement), because the instruction scoped the COUNT to the
# category and its CONDITION to the feature. Now that the prompt asks each
# worker to judge its OWN category's material, a thin category is a correct
# answer, and warning about it would make the honest outcome look like a
# shortfall on every run.
#
# So the TOTAL is the contract -- it is what the 2026-08-09 collapse actually
# violated (8 cases, one per category) -- and per category only two things are
# still reported when the total is met: a category that is EMPTY, and one that
# has COLLAPSED to less than half its floor. Half is ``_VOLUME_REFUSE_RATIO``,
# reused rather than invented: it is the same "materially short" line already
# drawn for the total. It is NOT measured on this axis and cannot be until
# suites generated under the new prompt exist, because no run before it was
# free to vary per category -- the two known-good 2026-08-04 runs bottom out at
# exactly the floor. Calibrate it against the first such suites; do not read it
# as evidence-backed today.
def _volume_floor_note(
    meta: object,
    cases: list,
    prep_id: str,
    *,
    ack: bool = False,
    post_dedup: bool = False,
    staged: int = 0,
) -> "tuple[str, str]":
    """Verdict on the generation VOLUME of a finalizing suite.

    Returns ``("", "")`` when this prep carries no volume contract or the suite
    honours it, ``("warn", note)`` for a soft shortfall, ``("acked", note)``
    when a refusal was overridden by ``volume_floor_ack`` on a prep this gate
    had ALREADY refused, and ``("refuse", markdown)`` when the suite is
    materially below the floor THIS prep's own payload demanded.

    ``staged`` is how many per-category rows the server ALREADY holds for this
    prep (0 on the merged route, where they were deliberately not used). It
    reorders the refusal's options ONLY -- no verdict turns on it; see
    ``_staged_resubmit_hint``. ``post_dedup=True`` is the WARNING-ONLY channel
    used once the suite is finalized: it returns a warning where it would
    otherwise refuse, and nothing at all otherwise.

    Never raises; on an unexpected error it fails OPEN (returns ``("", "")``)
    so a legitimate finalize is never blocked by the guard itself -- the same
    discipline as _fanout_incomplete_note. The rationale is in the comment
    block above this function.
    """
    try:
        verdict = _volume_verdict(meta, cases, ack=ack, post_dedup=post_dedup)
        if verdict is None:
            return "", ""
        facts = _volume_facts(verdict, meta, post_dedup)  # type: ignore[arg-type]
        if verdict.mode == "refuse":
            return verdict.mode, _volume_refusal_text(
                verdict, facts, prep_id, ack=ack, staged=staged
            )
        return verdict.mode, _volume_warning_text(verdict, facts, prep_id, post_dedup)
    except Exception:  # pragma: no cover - defensive, must never block a finalize
        logger.debug("volume floor gate failed", exc_info=True)
        return "", ""


def _volume_shortfall_detail(meta: object, cases: list) -> str:
    """One plain-text line naming every category below this prep's floor, or "".

    The workbook cell version of what _volume_floor_note renders as markdown.
    Kept SEPARATE rather than parsed out of that note, because the note is
    tester-facing prose that is rewritten whenever the wording is improved and a
    cell should not break when a sentence changes.

    It deliberately MIRRORS the gate's bucketing (normalize_category, unlabelled
    cases counting toward the total but toward no category) so the two can never
    disagree about which categories are short -- if that loop ever changes, both
    must change. Unlike the gate it applies NO slack: this is a record, not a
    verdict, so `7/8` belongs in it even though 7/8 is deliberately not worth a
    warning. Never raises.
    """
    try:
        if not isinstance(meta, dict) or not meta.get("volume_floor"):
            return ""
        try:
            floor = int(meta.get("volume_min_cases") or 0)
        except (TypeError, ValueError, OverflowError):
            return ""
        names = [
            str(n).strip()
            for n in (meta.get("volume_categories") or [])
            if str(n).strip()
        ]
        if floor <= 0 or not names:
            return ""
        counts: dict = {}
        for tc in cases or []:
            canon = normalize_category(getattr(tc, "category", None))
            if canon:
                counts[canon] = counts.get(canon, 0) + 1
        short = [(n, counts.get(n, 0)) for n in names if counts.get(n, 0) < floor]
        if not short:
            return ""
        listed = ", ".join(f"{n} {got}/{floor}" for n, got in short)
        return (
            f"{len(short)} of {len(names)} categories came back below the "
            f"{floor} cases per category this prep asked for: {listed}. "
            "The suite was accepted and exported anyway -- nothing blocked it. "
            "Re-generate the short categories if that was not deliberate."
        )
    except Exception:  # pragma: no cover - defensive; a note must never raise
        logger.debug("volume shortfall detail failed", exc_info=True)
        return ""


# Batch 4, LAYER 2 (QA_HOST_IMAGE_REQUIRE_RELEVANT, default OFF).
_IMAGE_GATE_MAX_NAMED = 8


def _relevance_refusal(
    mode: str, facts: str, prep_id: str, staged_hint: str, *, ignore_ack: bool
) -> str:
    """The markdown body of _image_relevance_gate for an ``acked`` override or a
    ``refuse``; *ignore_ack* means an ack arrived on the FIRST submit."""
    if mode == "acked":
        return _relevance_acked_text(facts)
    ignored_ack = ""
    if ignore_ack:
        ignored_ack = (
            "\n\n> \u26a0\ufe0f  `image_relevance_ack=true` arrived on the "
            "FIRST submit for this prep and was IGNORED: the tester cannot "
            "have seen these screens yet. Show them the finding above; if "
            "they confirm the suite is right anyway, resubmit it unchanged "
            "with the ack and it WILL be honoured."
        )
    return (
        "\u26d4 **Submission refused:** `QA_HOST_IMAGE_REQUIRE_RELEVANT` is "
        "on for this prep and this submission carries no clean per-image "
        "verdict for the screens it was generated from.\n\n"
        + facts
        + ignored_ack
        + f"\n\nNothing was discarded and prep `{prep_id}` is intact -- the "
        "prepared context and every staged category are still there.\n\n"
        "**Pick one:**\n"
        "1. If the screen really is the wrong one, capture or attach the "
        "correct screen and run `qa_prepare_test_cases` again WITH "
        "`proceed_anyway=true` -- a prep for this source is already open, "
        "so the duplicate-prep guard (default ON) refuses that call "
        "without it. Cases grounded on the wrong screen are the defect "
        "this refusal exists to catch.\n"
        "2. If you did read the screens and simply did not report it, "
        + _relevance_resubmit_option(prep_id, staged_hint)
        + "3. Or, ONLY if the TESTER has seen the finding above and confirms "
        "the suite is right anyway, resubmit it unchanged with "
        "`image_relevance_ack=true`. Ask them first -- do not decide that on "
        "your own judgement."
    )


def _relevance_acked_text(facts: str) -> str:
    """The ``acked`` body of _relevance_refusal."""
    return (
        "> \u26a0\ufe0f  Image relevance below the bar this prep asked "
        "for (refusal OVERRIDDEN by `image_relevance_ack=true`):\n"
        + "".join(f">   {line}\n" for line in facts.splitlines() if line.strip())
        + ">   The suite below was accepted as submitted. If that is not "
        "deliberate, capture or attach the correct screen and prepare "
        "again with `proceed_anyway=true` (a prep for this source is "
        "already open).\n\n"
    )


def _relevance_resubmit_option(prep_id: str, staged_hint: str) -> str:
    """Option 2 of the refusal: how to resend the missing verdicts."""
    # Staged route: the cases are already on the server, so the retry carries
    # the missing field ALONE. The wording is REUSED from
    # _staged_resubmit_hint rather than paraphrased, so this refusal and the
    # ambiguity one read the same way to a host. The array's SHAPE is handed
    # to that helper (`field_shape`) rather than appended here: appended, it
    # landed after the hint's closing "...cases the tester already reviewed"
    # and so after "and nothing else", dangling off the wrong noun.
    if staged_hint:
        return staged_hint + ".\n"
    return (
        f"resubmit the SAME suite with the SAME prep_id `{prep_id}` "
        "and a top-level `image_descriptions` array carrying one "
        "`relevant` verdict per image (the bare string `yes`, `no` or "
        "`unsure`) plus a one-line `relevance_reason`. On the "
        "per-category route send it in the finalize sidecar.\n"
    )


def _relevance_facts(forwarded: int, counts: dict, off: list) -> str:
    """The findings block of _image_relevance_gate: the off-topic screens, or the
    absence of any usable verdict."""
    if off:
        named = "\n".join(
            f"   - `{i.get('image_id', '?')}` \u2014 "
            f"{i.get('relevance_reason', '') or i.get('description', '')}"
            for i in off[:_IMAGE_GATE_MAX_NAMED]
        )
        if len(off) > _IMAGE_GATE_MAX_NAMED:
            named += f"\n   - (+{len(off) - _IMAGE_GATE_MAX_NAMED} more)"
        return (
            f"- **{len(off)} of {counts.get('images', 0)} described "
            'screen(s) came back `relevant: "no"`** -- your own chat '
            "model's verdict, MODEL-DERIVED and not verified by this "
            "server, which made no vision call:\n" + named
        )
    return (
        f"- **No usable `relevant` verdict came back for any image**, "
        f"although this prep forwarded {forwarded} screen(s) to your "
        "chat and asked for one per image. This server made no vision "
        "call of its own, so there is NO record that the screens were "
        "looked at -- and silence must not read as `yes`."
    )


def _relevance_verdicts(meta: object, result: object) -> "tuple | None":
    """What _image_relevance_gate needs to judge a submission, or None when the
    gate passes outright. Returns (forwarded, counts, off, refused_before).
    Called inside the gate's ``try``, so an error still fails OPEN there."""
    if not isinstance(meta, dict):
        return None
    if not meta.get("host_image_require_relevant"):
        return None
    if not meta.get("host_image_relevance"):
        return None
    try:
        forwarded = int(meta.get("captured_image_count") or 0) + int(
            meta.get("attached_image_count") or 0
        )
    except (TypeError, ValueError, OverflowError):
        forwarded = 0
    if forwarded <= 0:
        return None
    counts = image_relevance_counts(result)
    off = off_topic_images(result)
    if counts.get("ran") and not counts.get("no"):
        return None
    # Review H1: a resubmit is not asked to resend `image_descriptions`,
    # so an ABSENT field on a later submit means "the server did not ask
    # for it", NOT "the check was forfeited". Refusing there would reject
    # a suite THIS gate had already passed. The submit that DID answer
    # stamps `image_relevance_seen` -- the same carry-forward
    # discipline as serialize_adopted_state and the carried checklist
    # beside it. Only ABSENCE is forgiven: a verdict that IS resent is
    # judged normally, so a fresh `no` on a later round still refuses.
    if not counts.get("ran") and meta.get("image_relevance_seen"):
        return None
    return forwarded, counts, off, bool(meta.get("image_relevance_refused"))


# WHY _image_relevance_gate EXISTS: Batch 2 made the off-topic verdict visible;
# it still could not stop a suite grounded on the wrong screen from being
# finalized, exported and persisted. Under QA_HOST_IMAGE_REQUIRE_RELEVANT an
# operator may turn that disclosure into a refusal, exactly as
# QA_HOST_AMBIGUITY_REQUIRE_RESULT does for the boomeranged TICKET-7154
# preflight.
#
# IT REFUSES ON TWO THINGS AND ONLY TWO:
#   * any ``relevant: "no"`` -- the host itself says the screen is not about
#     this ticket;
#   * NO usable verdict on ANY image -- the job was shipped, screens really
#     were forwarded, and nothing came back, so there is no record that the
#     screens were even looked at. "Silence reads as cleared" is the precise
#     failure the ambiguity FIX 2 audit change exists to end.
#
# ``unsure`` PASSES, with Batch 2's warning. Refusing on uncertainty punishes
# the honest answer and trains a host to reply ``yes``, which would destroy the
# signal this whole feature is built on; ``no`` and "nothing came back" are
# unambiguous, and the latter is what the reported run produced.
#
# EVERY input is a META STAMP (``host_image_require_relevant``,
# ``host_image_relevance``, ``captured_image_count`` /
# ``attached_image_count``, ``image_relevance_refused``), never a live settings
# flag, so neither a mid-flow .env flip nor a launcher auto-update between
# prepare and submit can change an in-flight prep, and an envelope written
# before those stamps existed returns ``("", "")`` on the first guard. The
# verdicts themselves arrive already validated by
# host_mode.extract_host_image_descriptions -- the strict three-word identity
# map and the isinstance-str gate -- so nothing here re-reads an untrusted
# token.
#
# FORWARDED-SCREENS GUARD: enforcement needs both counts to be zero to opt out,
# i.e. it applies only when this server captured screens or the host attested
# chat attachments. Ticket images fetched from Jira are excluded on purpose:
# they came from the ticket itself, so "off-topic relative to that ticket" is a
# far weaker claim, and refusing a suite over one is the false positive this
# gate can least afford.
def _image_relevance_gate(
    meta: object,
    result: object,
    prep_id: str,
    *,
    ack: bool = False,
    staged_hint: str = "",
) -> "tuple[str, str]":
    """Verdict on the IMAGE RELEVANCE of a finalizing submission.

    Returns ``("", "")`` when this prep carries no enforcement contract or the
    submission honours it, ``("acked", note)`` when a refusal was overridden by
    ``image_relevance_ack`` on a prep this gate had ALREADY refused, and
    ``("refuse", markdown)`` otherwise.

    Never raises; on an unexpected error it fails OPEN (``("", "")``) so the
    guard itself can never block a legitimate finalize -- the same discipline as
    _volume_floor_note and _fanout_incomplete_note. The rationale is in the
    comment block above this function.
    """
    try:
        verdicts = _relevance_verdicts(meta, result)
        if verdicts is None:
            return "", ""
        forwarded, counts, off, refused_before = verdicts
        mode = "acked" if (ack and refused_before) else "refuse"
        facts = _relevance_facts(forwarded, counts, off)
        return mode, _relevance_refusal(
            mode, facts, prep_id, staged_hint, ignore_ack=ack and not refused_before
        )
    except Exception:  # pragma: no cover - defensive, must never block a finalize
        logger.debug("image relevance gate failed", exc_info=True)
        return "", ""


def _merge_category_rows(rows: list) -> "tuple[dict, int, dict]":
    """Merge accumulated per-category submission rows into ONE suite dict.

    Rows arrive in insertion order. Each row payload carries a validated
    ``test_cases`` list of JSON-native dicts (written by handle_submit_category).
    A reserved row is skipped defensively (none are written now that the round
    lives in the prep envelope, but the guard keeps a future magic key from being
    parsed as a suite). tc_ids are RENUMBERED to a global unique sequence before
    merging: every category restarts at TC-001 and TestSuite REJECTS duplicate
    tc_ids, so a literal "dedup by tc_id" across categories would wrongly discard
    almost every case. The throwaway ids are reassigned to TC-001..N by
    _finalize_generation's renumber, and genuine CONTENT duplicates are removed
    there by _dedupe_cases. Returns (merged_suite_dict, rows_used,
    id_map) where id_map maps each pre-merge tc_id to the global
    TC-NNNN assigned here."""
    merged_cases, case_row, used = _collect_category_cases(rows)
    renumbered = []
    id_map: dict = {}
    row_maps: dict[int, dict] = {}
    for i, c in enumerate(merged_cases, 1):
        c = dict(c)
        old_id = c.get("tc_id")
        new_id = f"TC-{i:04d}"
        if isinstance(old_id, str) and old_id:
            id_map.setdefault(old_id, new_id)
            row_maps.setdefault(case_row[i - 1], {})[old_id] = new_id
        c["tc_id"] = new_id
        renumbered.append(c)
    # 2026-08-30, review round 2 (C1/H2): THIS renumber is what used to break a
    # chained test-data reference, and it had to be fixed here rather than
    # downstream. `chained_from` holds a tc_id the host wrote, valid only inside
    # its own category; this loop rewrites every tc_id to a global TC-0001..N
    # sequence and left `chained_from` alone, so by the time anything downstream
    # saw it the id named either nothing (cleared, prerequisite lost) or -- worse,
    # when the host emitted 4-digit ids -- an UNRELATED case in another category.
    # Remapped per ROW, so a Negative case's ref to its own TC-001 can never land
    # on the Positive category's TC-001. A ref naming no case in its own category
    # is cleared and downgraded to `static`, matching the contract
    # tools/quality_checks' two chained-ref helpers already state: a dangling
    # chain must never reach an exporter as a broken prerequisite.
    for idx, c in enumerate(renumbered):
        _remap_chained_from(c, row_maps.get(case_row[idx]) or {})
    return {"test_cases": renumbered}, used, id_map


def _collect_category_cases(rows: list) -> "tuple[list, list[int], int]":
    """The usable cases of the staged rows, each stamped with its server-derived
    category. Returns (cases, row index of each case, rows used)."""
    merged_cases: list = []
    case_row: list[int] = []
    used = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = row.get("category_name", "")
        if name == "__round__":  # reserved-key guard (defensive; unused now)
            continue
        payload = row.get("payload") or {}
        cases = payload.get("test_cases")
        if not isinstance(cases, list):
            continue
        used += 1
        # F6: the row's category_name is SERVER-DERIVED (it is the argument the
        # tester's tool call carried, recorded per row) so it overrides whatever
        # a case claims about itself. Unresolvable -> left empty, never guessed.
        canon = normalize_category(name)
        for c in cases:
            if isinstance(c, dict):
                c = dict(c)
                # Unconditional: the server-derived name WINS even when it is
                # unresolvable, otherwise the host's own claim survives on the
                # path this comment calls server-derived (submit_category
                # persists model_dump(), which now carries `category`).
                c["category"] = canon or None
                c["category_source"] = "server" if canon else None
                merged_cases.append(c)
                # Which ROW (category) this case came from. Needed because a
                # `chained_from` written by the host names a case in ITS OWN
                # category, and every category numbers from TC-001 -- so the
                # mapping from old id to new id is only unambiguous per row.
                case_row.append(used - 1)
    return merged_cases, case_row, used


def _remap_chained_from(case: dict, row_map: dict) -> None:
    """Rewrite one merged case's ``chained_from`` ids through *row_map*, in place.

    Operates on RAW DICTS: at merge time the cases are still the JSON the host
    submitted, so `tools/quality_checks.resolve_chained_refs_to_stable` -- which
    needs a validated `TestCase` and its `stable_id` -- cannot be used yet. The
    two are complementary rather than redundant: this one carries a ref across
    the MERGE renumber (per category, where a tc_id is unambiguous), the other
    carries it across the FINAL risk-order renumber (whole suite, where tc_ids
    are already globally unique).

    A ref naming no case in this category is cleared and its strategy downgraded
    to ``static``, so no exporter renders a broken prerequisite. Never raises: a
    disclosure- or cosmetic-level field must not be able to fail a submit.

    Replaces the ``test_data`` LIST with fresh item dicts rather than editing the
    items in place. ``_merge_category_rows`` copies each case with ``dict(c)``,
    which is SHALLOW -- the item dicts are still the caller's, straight out of
    the submitted row payload -- so an in-place edit reached back into the
    caller's rows and a second merge over those same rows saw refs this pass had
    already rewritten, and cleared them outright. No live path merges the same
    rows twice today (``prep_store.load_submissions`` re-reads from SQLite on
    every call, and the merge sites are mutually exclusive branches), which is
    why nothing failed; a merge that quietly edits its input is a trap for the
    next caller regardless.
    """
    try:
        items = case.get("test_data")
        if not isinstance(items, list):
            return
        new_items: list = []
        changed = False
        for item in items:
            remapped = _remap_chained_item(item, row_map, case.get("tc_id"))
            changed = changed or remapped is not None
            new_items.append(item if remapped is None else remapped)
        if changed:
            case["test_data"] = new_items
    except Exception:
        logger.debug("merge: chained_from remap failed", exc_info=True)


def _remap_chained_item(item: object, row_map: dict, tc_id: object) -> "dict | None":
    """A copy of one chained ``test_data`` item with its ref remapped through
    *row_map*, or None when the item is not a chained ref and stays untouched."""
    ref = item.get("chained_from") if isinstance(item, dict) else None
    if (
        not isinstance(item, dict)
        or item.get("strategy") != "chained"
        or not isinstance(ref, str)
        or not ref
    ):
        return None
    mapped = row_map.get(ref)
    item = dict(item)
    if mapped is None:
        logger.info(
            "merge: clearing chained_from %r on %s -- no such case in its own category",
            ref,
            tc_id,
        )
        item["chained_from"] = None
        item["strategy"] = "static"
    else:
        item["chained_from"] = mapped
    return item


def _version_skew_note(staged: str, running: str) -> str:
    """Warning for a prep staged by one build and submitted to another.

    Returns "" when the versions match or either is unknown, so callers can use it
    unconditionally. Never raises.

    2026-08-03: the previous wording asserted ONE cause -- "the server updated
    mid-flow" -- for a condition that has two, and the unstated one is worse. The
    first production run of v1.34.0 staged its prep on a SEPARATE install (a dev
    checkout reporting v0.1.0) and submitted it to the packaged v1.34.0 server,
    because both were registered in the same client and the agent split the flow
    across them. Two installs mean two `.env` files and two sets of feature flags,
    so the suite was prepared under one configuration and finalized under another.
    The old closing line -- "the suite below is fine unless something looks wrong"
    -- waved exactly that through.

    So: state the observed fact, name BOTH causes, mark which one is harmful, and
    let the version SHAPE point at the likely one. A self-update moves between
    released versions; a dev checkout reports 0.x, which is the strong hint that a
    second install is involved. Extracted from handle_submit_suite so the wording
    is directly testable rather than only reachable through a full submit.
    """
    try:
        staged = str(staged or "").strip()
        running = str(running or "").strip()
        if not staged or not running or staged == running:
            return ""
        note = (
            f"\n> \u26a0\ufe0f  Version mismatch: this prep was staged by "
            f"v{staged} and is being submitted to v{running}. Either this install "
            "self-updated between the two calls (harmless), or the prep came from "
            "a DIFFERENT install of this server (not harmless: a second install "
            "has its own `.env`, so the suite was prepared under one set of "
            "feature flags and finalized under another)."
        )
        if staged.startswith("0."):
            note += (
                " The staging version looks like a development checkout rather "
                "than a release, which points at the second case: check whether "
                "more than one qa-agents server is registered in your client, and "
                "run the whole flow against ONE of them."
            )
        return note + (
            " The suite below was still built from the cases you submitted; "
            "re-run `qa_generate_test_cases` end to end on a single install if "
            "anything looks inconsistent.\n"
        )
    except Exception:  # pragma: no cover - defensive; must never break a submit
        logger.exception("_version_skew_note failed")
        return ""


_CODE_SKEW_BODY = (
    " The prepared context in this prep -- the grounded ticket text, "
    "the acceptance criteria, the category specs -- was built by the "
    "OLD code and is replayed here as it was stored; restarting the "
    "server did not rebuild it. If the change was to how ticket text "
    "is read or grounded, this suite still carries the old behaviour. "
    "Run `qa_prepare_test_cases` again on the same ticket to rebuild "
    "the context, then generate against the NEW prep_id.\n"
)


def _code_skew_note(staged: str, running: str, *, version_matched: bool) -> str:
    """Warning for a prep staged by DIFFERENT CODE than is now running.

    Returns "" when the fingerprints match or either is unknown, so callers can
    use it unconditionally. Never raises.

    F4 (2026-08-15). This is the case _version_skew_note structurally cannot
    see. That note compares ``meta.app_version`` against ``_BOOT_VERSION``, and
    a developer checkout reports ``0.1.0`` before and after any edit -- so
    through exactly the changes a tester most needs to know about (a grounding
    fix, a sanitisation fix), the versions match and it stays silent. Meanwhile
    ``data/suites.db`` is shared by every server process on the machine and a
    prep survives a restart, so "restart the server and submit the prep_id you
    already have" reuses pre-fix context against fixed code and produces
    pre-fix output with nothing disclosing it. That is what happened on the
    TICKET-5645 validation run.

    WARNING, NEVER A REFUSAL -- the same stance as _version_skew_note, and for
    the same reason: the prepared work is the tester's, and most code changes
    do not invalidate a staged prep. What is fixed here is the SILENCE.

    ``version_matched`` shapes the wording only. When the versions ALSO differ,
    _version_skew_note has already said the builds are different and named both
    causes, so this adds the one fact that note cannot supply (the prepared
    context itself is stale) without repeating it.
    """
    try:
        staged = str(staged or "").strip()
        running = str(running or "").strip()
        if not staged or not running or staged == running:
            return ""
        head = (
            "\n> \u26a0\ufe0f  Stale prepared context: this prep was staged by a "
            f"DIFFERENT BUILD of the generation code (`{staged[:12]}`) than the "
            f"one now running (`{running[:12]}`)."
        )
        if version_matched:
            head += (
                " The version numbers are identical, so this is a code change "
                "on the same install -- typically a fix applied between "
                "`qa_prepare_test_cases` and this submit, with the server "
                "restarted in between."
            )
        return head + _CODE_SKEW_BODY
    except Exception:  # pragma: no cover - defensive; must never break a submit
        logger.exception("_code_skew_note failed")
        return ""


def _prep_status_finalize_hint() -> str:
    """The finalize advice appended to the `qa_prep_status` reply.

    Fix 2 (2026-08-03): with the duplicate review ON, an empty `suite_json` is the
    call that FORFEITS it, so presenting that as the PRIMARY finalize and the
    sidecar as an afterthought steered hosts into losing a review the tester had
    switched on -- observed on run3 (TICKET-5645), where 98 cases from 8 mutually
    blind workers got no cross-category review at all. Both routes stage the same
    rows, so the sidecar is equally crash-safe; only the review differs.
    """
    return (
        # D3 (2026-08-21): "PRIMARY / ALTERNATIVE" against the prepare
        # instruction's "recommended / a supported route, not a shortcut" was
        # the honest-peers framing delivered by halves. Same recommendation,
        # same words as host_mode.
        "\nRECOMMENDED finalize (Path A, crash-safe, keeps your review, and "
        "the only route the server duplicate prescreen runs on): when "
        "ready=yes, call `qa_submit_suite` with the small review SIDECAR "
        "object described in your preparation instructions (no "
        "`test_cases`). Finalizing with an empty `suite_json` instead is "
        "equally crash-safe but FORFEITS the duplicate review -- so send the "
        "sidecar even when you found nothing, because an EMPTY review field "
        "still counts as a review. The SUPPORTED ALTERNATIVE "
        "(Path B, one merged `suite_json`) does not need ready=yes, but "
        "nothing is saved until that single call, so an interrupted chat "
        "loses every category."
    )


# B1 (2026-08-21, TICKET-5138): the two prep_status states a polling host can switch
# on. A second editor process re-used a prep_id whose suite had finalized 11s
# earlier and polled qa_prep_status 122 times at 1s (21:10:22 -> 21:12:26, every
# reply 1-12 ms) before giving up. _finalized_reply was returned all 122 times and
# DID name the suite -- but it is prose in a different shape from the status reply
# a loop parses, so there was no FIELD a loop condition could read as terminal.
_PREP_STATE_IN_PROGRESS = "in_progress"
_PREP_STATE_FINALIZED = "finalized"


def _prep_status_state_line(state: str) -> str:
    """The one machine-readable field a polling host can switch on.

    COMPATIBILITY: this is a NEW bullet. `ready to finalize (Path A)`, `staged`,
    `missing` and `unrecognized names` keep their exact wording, order, values and
    meaning on the non-finalized path, and the finalized path keeps emitting NONE
    of them (pinned by tests/test_prep_finalized_stamp.py, which asserts "staged:"
    is absent) -- no fake `staged: 0/0` is manufactured for a finished prep,
    because that would redefine those fields. A caller that ignores this line
    behaves exactly as it does today."""
    return f"- **state:** {state}\n"


def _prep_status_finalized_reply(prep_id: str, finalized_note: str) -> str:
    """The already-finalized notice, in the prep-status SHAPE, with a terminal
    state. ``finalized_note`` is _finalized_reply's text, used unchanged."""
    return (
        f"## Prep status (`{prep_id}`)\n\n"
        + _prep_status_state_line(_PREP_STATE_FINALIZED)
        + "- **terminal:** yes -- this prep will never change again. STOP polling "
        "`qa_prep_status` for this prep_id; nothing is pending.\n\n" + finalized_note
    )


async def handle_prep_status(prep_id: str) -> str:
    """Report staged vs expected categories for a prep_id. Never raises."""
    # UNTRUSTED at every one of these entry points: the id is whatever the
    # host sent, and it is echoed back in refusals and next-step
    # instructions, so an unsanitised one can close its code span and write
    # markdown into text the model then follows. Gated HERE, once, rather
    # than at the eighteen interpolations downstream.
    prep_id = safe_prep_id(prep_id)
    if not prep_id:
        return "⚠️ Missing prep_id."
    try:
        # Fix 5b: a finalized prep now LOADS, so every reader must check the stamp
        # before rehydrating. Without this, prep_status would report `staged: 0/8`
        # and then recommend the Path A finalize -- actively instructing a re-stage
        # of all 8 categories for a suite that is already finished.
        # B1 (2026-08-21): wrapped in the status shape so the finalized case is a
        # readable TERMINAL state and not only prose. The note itself is unchanged.
        envelope, refusal = await _load_open_prep(
            prep_id, wrap_final=_prep_status_finalized_reply
        )
        if refusal:
            return refusal
        expected = _prep_expected_names(envelope)
        rows_res = await prep_store.load_submissions(prep_id)
        # Same class as the load_prep reads above: `content` alone cannot
        # tell 'nothing staged' from 'the store could not be read', and
        # reporting a store failure as `staged: 0/8` invites a re-stage of
        # all eight categories.
        _store_note = _prep_store_error_reply(prep_id, rows_res)
        if _store_note:
            return _store_note
        rows = rows_res.get("content") or []
        staged_names = [
            str(r.get("category_name") or "") for r in rows if isinstance(r, dict)
        ]
        status = prep_status_view(expected=expected, staged_raw_names=staged_names)
        return _prep_status_reply(prep_id, status)
    except Exception as exc:
        logger.exception("handle_prep_status failed")
        _common._capture_error(exc, "qa_prep_status")
        return f"⚠️ Could not read prep status: {exc}"


def _prep_expected_names(envelope: dict) -> list:
    """Expected category names for a prep: recorded, else derived from `prepared`."""
    meta = envelope.get("meta") or {}
    expected = list(meta.get("expected_categories") or [])
    if not expected and not meta.get("parallel_fanout"):
        try:
            prepared = deserialize_prepared(envelope.get("prepared") or {})
            expected = expected_category_names(prepared)
        except Exception:
            expected = []
    return expected


def _prep_status_reply(prep_id: str, status: dict) -> str:
    """Render the in-progress prep-status reply from the status view."""
    ready = "yes" if status.get("ready") else "no"
    missing = ", ".join(f"`{m}`" for m in status.get("missing") or []) or "(none)"
    staged = ", ".join(f"`{s}`" for s in status.get("staged") or []) or "(none)"
    # F6: the staged names are host-supplied, so this line is clipped (length,
    # fence, newlines) and its COUNT capped with the remainder disclosed; an
    # unbounded echo let a staged name inject server prose into the reply.
    unrec_line = _reply._unrecognized_names_line(
        status.get("unrecognized") or [],
        prefix="- **unrecognized names:** ",
        suffix="\n",
    )
    return (
        f"## Prep status (`{prep_id}`)\n\n"
        + _prep_status_state_line(_PREP_STATE_IN_PROGRESS)
        + f"- **ready to finalize (Path A):** {ready}\n"
        f"- **staged:** {status.get('staged_count', 0)}/"
        f"{status.get('expected_count', 0)} — {staged}\n"
        f"- **missing:** {missing}\n" + unrec_line + _prep_status_finalize_hint()
    )


def _all_jobs_reply(envelope: Any, prepared: Any, prep_id: str) -> str:
    """The "all" form: every job packet in one fenced JSON, step zero first."""
    import json as _json

    batch = build_category_jobs_batch(prepared, prep_id)
    if batch is None:
        return f"⚠️ No category jobs available for prep_id `{prep_id}`."
    # F3 (2026-08-30): serve the step-zero jobs HERE, in the one call the
    # instructions tell a host to make. Stamped at prepare time -- see
    # the persist site in _prepare_generation for the defect this closes.
    # FIRST key in the dict on purpose: a host reading the JSON top-down
    # meets the blocking preflight before the categories. An envelope
    # written before this key existed simply carries none, and that
    # reply is byte-identical to today's.
    _sz = envelope.get("step_zero") if isinstance(envelope, dict) else None
    if isinstance(_sz, dict) and _sz.get("jobs_to_run"):
        batch = {"step_zero": _sz, **batch}
    _n = len(batch["jobs"])
    return (
        f"## Category jobs — ALL {_n} categories (one fetch)\n\n"
        f"`prep_id`: `{prep_id}`\n\n"
        "`shared` applies to EVERY job; `shared` plus one `jobs[]` "
        "entry is the same packet the single-category form returns. "
        # D3 (2026-08-21): this line used to ask the host to launch
        # one same-session sub-context per `jobs[]` entry, all at once.
        # It was the SECOND place this server asked for that, and the
        # one a host reads at the exact moment it decides how to
        # generate -- so retiring the ask in host_mode alone would have
        # left the contradiction here. The retired wording is NOT quoted
        # back: a tombstone comment is how an absence grep gets armed by
        # the very change that satisfied it.
        "**Run every `step_zero` job FIRST, before you generate a single "
        "case.** The ambiguity preflight is `blocking: true`, and the "
        "acceptance-criteria job produces the ac_ids each case's "
        "`requirement_id` has to cite — a case written before those "
        "ids exist can only be tagged null, and the only way to fix that "
        "afterwards is to regenerate the suite. Then generate each "
        "`jobs[]` entry and call `qa_submit_category` for it as soon as "
        "its cases are written — do not fetch categories one call at "
        "a time.\n\n"
        "```json\n" + _json.dumps(batch, ensure_ascii=False, indent=2) + "\n```\n"
    )


def _single_job_reply(prepared: Any, prep_id: str, name: str) -> str:
    """The one-category form: that job packet as fenced JSON, or a refusal."""
    import json as _json

    job = build_category_job(prepared, prep_id, name)
    if job is None:
        return (
            f"⚠️ Unknown category `{_reply._clip_echo(name, 80)}` "
            f"for prep_id `{_reply._clip_echo(prep_id, 80)}`. "
            "Use a name from orchestration.expected_categories / categories[].name."
        )
    return (
        f"## Category job — **{job.get('category_name')}**\n\n"
        f"`prep_id`: `{prep_id}`\n\n"
        "```json\n" + _json.dumps(job, ensure_ascii=False, indent=2) + "\n```\n"
    )


async def handle_get_category_job(prep_id: str, category_name: str) -> str:
    """Return one self-contained category job packet as fenced JSON. Never raises."""
    # UNTRUSTED at every one of these entry points: the id is whatever the
    # host sent, and it is echoed back in refusals and next-step
    # instructions, so an unsanitised one can close its code span and write
    # markdown into text the model then follows. Gated HERE, once, rather
    # than at the eighteen interpolations downstream.
    prep_id = safe_prep_id(prep_id)
    category_name = (category_name or "").strip()
    if not prep_id:
        return "⚠️ Missing prep_id."
    if not category_name:
        return "⚠️ Missing category_name."
    try:
        # Fix 5b: without the finalized refusal, a finalized prep would be served
        # a FRESH worker packet and touch_prep would slide its TTL -- re-arming
        # exactly the regenerate-after-finish loop this fix exists to stop.
        envelope, refusal = await _load_open_prep(prep_id)
        if refusal:
            return refusal
        prepared = deserialize_prepared(envelope.get("prepared") or {})
        # 2026-07-31 incident: fetching a worker packet is real orchestration
        # activity -- that run fetched 8 and staged none. prep_store gates the
        # write (no-op unless QA_PREP_SLIDING_TTL_ENABLED or
        # QA_PREP_DISCLOSE_UNFINISHED is on: the TTL needs it to slide, the
        # disclosure needs it to SEE this exact shape) and never raises, so the
        # fetch is never blocked by it.
        await prep_store.touch_prep(prep_id)
        if category_name.lower() in ("all", "*"):
            return _all_jobs_reply(envelope, prepared, prep_id)
        return _single_job_reply(prepared, prep_id, category_name)
    except PrepSerdeError as exc:
        return f"⚠️ Could not read this prep: {exc}"
    except Exception as exc:
        logger.exception("handle_get_category_job failed")
        _common._capture_error(exc, "qa_get_category_job")
        return f"⚠️ Could not build category job: {exc}"
