"""Moved from handlers (refactor P2): generate."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple
from urllib.parse import urlparse

from agents.host_ac import AC_JOB
from agents.host_checklist import CHECKLIST_JOB
from agents.host_image import IMAGE_JOB, IMAGE_PREFLIGHT_JOB, IMAGE_RELEVANCE_JOB
from agents.host_jobs import attach_ambiguity_job, attach_jobs
from agents.host_orchestration import (
    _parallel_fanout_on,
    build_prepare_payload,
    expected_category_names,
    prepared_case_bounds,
)
from agents.host_prep_serde import serialize_prepared
from agents.test_scenario_agent import _prepare_generation
from config.settings import settings
from handlers import _common, _elicit, capture, image_gate, jira, prep_status
from tools import prep_store
from tools.build_fingerprint import code_fingerprint
from tools.jira_fetcher import fetch_url_content
from tools.jira_mcp import (
    bare_issue_key,
    issue_url_for_key,
    looks_like_jira_url,
    not_connected_message,
)
from tools.rag_store import add_to_corpus, query_corpus, replace_source_entries
from tools.suite_store import find_recent_suite_by_source, load_suite
from tools.swagger_fetcher import fetch_openapi_spec, looks_like_openapi_url
from tools.ui_extractor import extract_ui_elements
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mcp_handlers")

# --------------------------------------------------------------------------- #
# v1.97.0 cursor-hardening (item 3a/8): duplicate-content and recent-suite
# advisories. Both are WARN-only -- neither ever blocks generation or submit.
# --------------------------------------------------------------------------- #
_DUP_CORPUS_QUERY_CHARS_CAP = 4000
_DUP_CORPUS_TOP_K = 10
_RECENT_SUITE_WARN_WINDOW_S = 86400


def _case_content_hash(tc: object) -> str:
    """SHA-256 hex digest of the EXACT text _persist_suite_to_corpus writes to
    the corpus for this case, so the two always agree on byte-identical
    content. Never raises (returns "" on any failure)."""
    try:
        steps_text = "\n".join(
            f"{s.step_number}. {s.action} -> {s.expected_result}" for s in tc.steps
        )
        content = f"{tc.title}\n{steps_text}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    except Exception:
        logger.debug("_case_content_hash failed", exc_info=True)
        return ""


async def _duplicate_case_note(cases: list, source_url: str = "") -> tuple:
    """(note, counts) -- a WARN-only, hash-based duplicate check run at
    qa_submit_suite (against the prior suite for the same source, computed
    BEFORE that suite is overwritten) and best-effort at qa_submit_category.
    Checks the most recent suite for this source for exact overlap, then runs
    ONE bounded query_corpus call (capped to keep a large suite's submit
    cheap) and intersects returned entries' content hashes. Returns ("",
    counts-with-zeros) when nothing matches. Never raises."""
    counts = {"suite_id": "", "suite_n": 0, "corpus_n": 0, "m": 0}
    try:
        hashes = {_case_content_hash(tc) for tc in (cases or [])}
        hashes.discard("")
        m = len(cases or [])
        counts["m"] = m
        if not hashes:
            return "", counts
        prior_suite_id, suite_n = await _prior_suite_overlap(hashes, source_url)
        counts["suite_id"] = prior_suite_id
        counts["suite_n"] = suite_n
        corpus_n = await _corpus_overlap(cases, hashes)
        counts["corpus_n"] = corpus_n
        if suite_n and prior_suite_id:
            return (
                f"{suite_n}/{m} cases identical to suite `{prior_suite_id}`",
                counts,
            )
        if corpus_n:
            return f"{corpus_n}/{m} cases match existing corpus entries", counts
        return "", counts
    except Exception:
        logger.debug("_duplicate_case_note failed", exc_info=True)
        return "", counts


async def _prior_suite_overlap(hashes: set, source_url: str) -> tuple[str, int]:
    """(prior suite id, how many of *hashes* it already holds) for *source_url*."""
    if not source_url:
        return "", 0
    recent = await find_recent_suite_by_source(source_url, window_s=None)
    prior = (recent.get("content") or {}) if not recent.get("error") else {}
    prior_suite_id = str(prior.get("suite_id") or "")
    if not prior_suite_id:
        return prior_suite_id, 0
    loaded = await load_suite(prior_suite_id)
    prior_suite = loaded.get("content")
    if prior_suite is None:
        return prior_suite_id, 0
    prior_hashes = {
        _case_content_hash(tc)
        for tc in (getattr(prior_suite, "test_cases", None) or [])
    }
    return prior_suite_id, len(hashes & prior_hashes)


async def _corpus_overlap(cases: list, hashes: set) -> int:
    """How many of *hashes* one bounded corpus query returns; 0 on any failure."""
    try:
        titles = " ".join(str(getattr(tc, "title", "") or "") for tc in (cases or []))
        titles = titles[:_DUP_CORPUS_QUERY_CHARS_CAP]
        if not titles.strip():
            return 0
        result = await query_corpus(
            titles, entry_type="test_case", top_k=_DUP_CORPUS_TOP_K
        )
        hits = (result.get("content") or []) if not result.get("error") else []
        corpus_hashes = {
            hashlib.sha256(str(h.get("content") or "").encode("utf-8")).hexdigest()
            for h in hits
        }
        return len(hashes & corpus_hashes)
    except Exception:
        logger.debug("_duplicate_case_note corpus check failed", exc_info=True)
        return 0


async def _recent_suite_warning_note(source_url: str, window_s: float) -> str:
    """\"> ...\" WARN when a suite already exists for source_url within
    window_s (item 8) -- restores what _find_recent_duplicate_suite gave up
    in 1.96.0. Never blocks generation. Never raises."""
    try:
        if not source_url:
            return ""
        recent = await find_recent_suite_by_source(source_url, window_s=window_s)
        if recent.get("error"):
            return ""
        row = recent.get("content")
        if not row:
            return ""
        suite_id = str(row.get("suite_id") or "")
        created_at = row.get("created_at")
        if not suite_id or not created_at:
            return ""
        age_s = max(0.0, time.time() - float(created_at))
        age = _ago_label(age_s)
        return (
            f"> \u2139\ufe0f Suite `{suite_id}` for this ticket was generated "
            f"{age} ago. Generation continues."
        )
    except Exception:
        logger.debug("_recent_suite_warning_note failed", exc_info=True)
        return ""


def _corpus_rows(cases: list, source_key: str, feature_text_capped: str) -> list:
    """(content, metadata) for each case that serializes; one that does not is
    logged and skipped."""
    rows: list = []
    for tc in cases:
        try:
            steps_text = "\n".join(
                f"{s.step_number}. {s.action} -> {s.expected_result}" for s in tc.steps
            )
            content = f"{tc.title}\n{steps_text}"
            metadata = {
                "feature": tc.module,
                "module": tc.module,
                "tc_id": tc.tc_id,
                "stable_id": tc.stable_id,
            }
            if source_key:
                # The identity replace_source_entries matches on the next
                # generation of this same source.
                metadata["source_key"] = source_key
            if feature_text_capped:
                metadata["feature_text"] = feature_text_capped
        except Exception:
            logger.warning("RAG: could not serialize a test case for corpus — skipping")
            continue
        rows.append((content, metadata))
    return rows


async def _persist_suite_to_corpus(
    suite: object, feature_text: str = "", source_url: str = ""
) -> None:
    """Write each generated test case into the RAG corpus (QW-6 / I-014 / F7).

    This is the *write* half of the RAG loop; query_corpus in
    test_scenario_agent is the read half. Best-effort and never disrupts the
    tool call: a serialization or disk error for one case is logged and
    skipped (add_to_corpus is itself never-raise).
    """
    cases = getattr(suite, "test_cases", None) or []
    feature_text_capped = (feature_text or "").strip()[
        : image_gate._FEATURE_TEXT_METADATA_CAP
    ]
    # Batch C item 2 (2026-08-09): REPLACE, do not append. Re-generating the same
    # ticket used to add a whole second copy of its cases: the corpus grew
    # 571 -> 676 docs in ONE day of re-runs, and a later prepare saw 5/5 RAG hits
    # flagged as duplicate risk -- i.e. the retrieval half had started grounding
    # new generations on its own echoes, and the only bound on the store was
    # QA_RAG_MAX_ENTRIES pruning the OLDEST entries, which is a cap, not
    # de-duplication.
    #
    # ORDER MATTERS (review C2): the fresh rows are written FIRST and the
    # superseded ones are pruned AFTER, keyed on this source and always sparing
    # the ids just written. A delete-then-write would have destroyed the previous
    # good suite whenever the new one turned out to be empty or entirely
    # unserializable -- i.e. exactly when the tester still needs the old one. In
    # the worst case this order leaves BOTH copies (a prune failure is logged,
    # never raised), which is the pre-2026-08-09 behaviour and so never a
    # regression.
    #
    # PARTIAL WRITES: NEWEST WINS (review N4). If some cases serialized and
    # others did not, the prune still runs, so this source keeps the cases that
    # were written NOW and loses the older copy of the ones that were not. The
    # alternative -- prune only on a complete write -- was rejected: a single
    # unserializable case would then leave the duplicate pair in place forever,
    # which is the very defect this change exists to end, and the corpus is a
    # grounding aid, not a system of record (the suite itself lives in
    # suite_store and the .xlsx export).
    # feature_text matters here: without it a pasted-description suite has no
    # identity and silently falls back to append.
    source_key = image_gate._corpus_source_key(source_url, feature_text)
    rows = _corpus_rows(cases, source_key, feature_text_capped)
    fresh_ids = await _write_corpus_rows(rows)
    await _prune_superseded_cases(source_key, fresh_ids)


async def _write_corpus_rows(rows) -> list:
    """Write each row to the corpus; return the ids of the rows written."""
    written = 0
    fresh_ids: list = []
    for content, metadata in rows:
        try:
            result = await add_to_corpus("test_case", content, metadata)
            if not result.get("error"):
                written += 1
                # Keep the id so the prune below spares the row it just wrote.
                _new_id = str((result.get("content") or {}).get("id") or "")
                if _new_id:
                    fresh_ids.append(_new_id)
        except Exception:
            logger.warning("RAG: add_to_corpus failed for a test case — ignoring")
    if written:
        logger.info("RAG: persisted %d test case(s) to corpus", written)
    return fresh_ids


async def _prune_superseded_cases(source_key: str, fresh_ids: list) -> None:
    """Drop the older copies of a source's cases, sparing the fresh ids."""
    if source_key and fresh_ids:
        _replaced = await replace_source_entries(
            "test_case", source_key, keep_ids=fresh_ids
        )
        _removed = int(((_replaced or {}).get("content") or {}).get("removed") or 0)
        if _removed:
            logger.info("RAG: removed %d superseded case(s) for this source", _removed)
    elif source_key:
        logger.debug(
            "RAG: nothing was persisted for this source -- the previously stored "
            "cases were left exactly as they were"
        )
    else:
        logger.debug(
            "RAG: this suite has no source key (no http(s) source URL and no "
            "feature text) -- appending, since the corpus cannot de-duplicate a "
            "source it cannot name"
        )


# --------------------------------------------------------------------------- #
# Host-mode ("boomerang") test-case generation -- ops-3d
#
# In host mode the 8-category fan-out runs in the tester's OWN chat model on any
# MCP host. handle_prepare_test_cases runs the FRONT half (fetch + preflight +
# gate + _prepare_generation) and returns a grounded payload + prep_id; the BACK
# half (submit) lands in ops-3d-1b. This block is DEAD CODE behind
# QA_GENERATION_MODE (default "server") until ops-3d-3 wires routing -- nothing
# here touches the server path (it is a NEW helper, not a refactor of
# handle_generate_test_cases).
# --------------------------------------------------------------------------- #


@dataclass
class _Grounding:
    """Result of _ground_and_gate when grounding + gating passed: the inputs
    _prepare_generation needs. Deliberately a SEPARATE helper rather than a
    refactor of handle_generate_test_cases, so applying this additive batch has
    zero server-path blast radius."""

    url_content: dict | None = None
    ui_content: dict | None = None
    openapi_text: str | None = None


def _page_fetch_refusal(text: str, url_content: dict) -> str | None:
    """The markdown to relay when the ticket / page fetch failed in a way that
    must stop generation, else None (a generic page may still fall through)."""
    # needs_jira_mcp: the agent has not fetched the ticket yet. The
    # error IS the directive -- relay it verbatim so the agent acts
    # on it, instead of proceeding with no ticket content (which is
    # what fabricated suites from an empty Jira SPA shell).
    if url_content.get("needs_jira_mcp"):
        return str(url_content.get("error") or not_connected_message())
    hint = jira._jira_config_hint(text)
    if hint:
        return hint
    # 2026-08-31. Every OTHER error used to fall through to
    # generation. That silently swallowed the two anti-fabrication
    # refusals normalize_issue_payload raises with
    # needs_jira_mcp=False -- "that payload has no `fields` object"
    # and "no summary, no description and no acceptance criteria" --
    # and a full suite was written from nothing. When the SOURCE is
    # Jira, an unreadable source is a refusal, not a warning. A
    # generic page that failed to fetch still falls through: the
    # tester's own words may carry the feature.
    if looks_like_jira_url(text) or url_content.get("jira_page_without_issue"):
        return str(url_content.get("error"))
    return None


async def _extract_page_ui(
    text: str, attached_images: list | None, url_content: dict
) -> dict | None:
    """UI extraction for a fetched page; None when skipped or when it fails."""
    if jira._skip_ui_extraction(text, attached_images):
        logger.info(
            "ui_extractor: skipped -- %s carries no app UI to extract",
            "the Jira ticket page",
        )
        return None
    try:
        return await extract_ui_elements(text, prefetched=url_content)
    except Exception:
        logger.debug("mcp: UI extraction failed -- continuing", exc_info=True)
        return None


@dataclass(frozen=True)
class _HostCallbacks:
    """The host's interactive callbacks, passed around as one value."""

    choose: _common.ChooseCb = None
    ask_text: _elicit.AskCb = None
    progress: _common.ProgressCb = None


async def _grounding_fetch(
    text: str,
    *,
    attached_images: list | None,
    callbacks: _HostCallbacks,
    jira_content_json: str,
) -> "str | _Grounding":
    """URL/Jira fetch: setup hint, _jira_preflight, Swagger ingest and UI
    extraction. Returns a markdown STRING to short-circuit or a _Grounding."""
    choose, ask_text, progress = (
        callbacks.choose,
        callbacks.ask_text,
        callbacks.progress,
    )
    url_content = None
    ui_content = None
    openapi_text = None
    if _common._is_url(text):
        hint = jira._jira_config_hint(text)
        if hint:
            return hint
        preflight = await jira._jira_preflight(
            text, ask_text=ask_text, choose=choose, progress=progress
        )
        if preflight is not None:
            return preflight
        # QA_SWAGGER_ENABLED was DELETED on 2026-08-13 (flag-surface
        # reduction, batch 6) and hardcoded ON -- the value the distribution
        # shipped and the README advertises. looks_like_openapi_url is the
        # only gate left.
        if looks_like_openapi_url(text):
            await _common._emit(progress, "\U0001f517 Fetching the OpenAPI spec…")
            spec_result = await fetch_openapi_spec(text)
            if not spec_result.get("error"):
                openapi_text = spec_result.get("summary") or None
        if openapi_text is None:
            page = await _fetch_page_content(
                text, attached_images, jira_content_json, progress
            )
            if isinstance(page, str):
                return page
            url_content, ui_content = page

    return _Grounding(
        url_content=url_content,
        ui_content=ui_content,
        openapi_text=openapi_text,
    )


async def _fetch_page_content(
    text: str, attached_images: list | None, jira_content_json: str, progress
) -> "str | tuple":
    """Fetch the ticket / page and extract its UI. Returns a refusal STRING or
    the (url_content, ui_content) pair."""
    await _common._emit(progress, "\U0001f517 Fetching the ticket / page\u2026")
    # jira_content_json is the BACK half of the Jira boomerang: when the
    # calling agent has already fetched the issue with its own
    # mcp__atlassian__getJiraIssue, it hands the raw JSON back here and
    # fetch_url_content normalizes it instead of asking again. Empty on
    # the first call, and ignored entirely for a non-Jira URL.
    # Called with ONE argument when there is nothing to hand back, so
    # every existing single-parameter test double for fetch_url_content
    # keeps working unchanged.
    if jira_content_json:
        url_content = await fetch_url_content(text, jira_content=jira_content_json)
    else:
        url_content = await fetch_url_content(text)
    if url_content.get("error"):
        refusal = _page_fetch_refusal(text, url_content)
        if refusal is not None:
            return refusal
    ui_content = await _extract_page_ui(text, attached_images, url_content)
    return url_content, ui_content


async def _grounding_gates(
    *, proceed_anyway: bool, attached_images: list | None, audit_source: str
) -> None:
    """The (fail-safe) ambiguity gate: audit-only, the review runs on the host."""
    if proceed_anyway or attached_images:
        return
    # The server-side ambiguity pre-pass is GONE (P2-J, 2026-08-16), so
    # there is no branch here any more and no progress line announcing a
    # check this process does not perform. The audit event is KEPT: an
    # operator reading the trail must still see that the gate was
    # DELEGATED rather than silently absent.
    logger.info(
        "prepare: ambiguity gate runs on the host (no server-side classifier call)"
    )
    await _common._audit(
        "mcp_ambiguity_gate",
        detail={"source": audit_source, "skipped": "host_ambiguity_review"},
    )


async def _ground_and_gate(
    text: str,
    *,
    attached_images: list | None = None,
    proceed_anyway: bool = False,
    choose: _common.ChooseCb = None,
    ask_text: _elicit.AskCb = None,
    progress: _common.ProgressCb = None,
    audit_source: str = "prepare",
    jira_content_json: str = "",
) -> "str | _Grounding":
    """Run the shared front half: URL/Jira fetch, _jira_preflight, Swagger
    ingest, UI extraction and the (fail-safe) ambiguity gate. Returns a markdown
    STRING to short-circuit (setup hint / preflight / clarifying questions) or a
    _Grounding to proceed.

    Comment reconciliation left that list on 2026-08-15: dead-code deletion
    batch D5 deleted tools/comment_reconciler.py, the amendment gate that could
    short-circuit here, and the suppress_comment_llm parameter that governed it.
    A ticket's comment thread now reaches generation exactly as it did before
    the pipeline existed -- as the raw "## Comments" dump jira_mcp leaves in the
    ticket text -- which is what every install has actually done since the seam
    was pinned False on 2026-08-14. See docs/RETIRED_CAPABILITIES.md section 4.

    A faithful re-derivation of the front half of handle_generate_test_cases; it
    is intentionally a SEPARATE helper rather than a refactor of that handler, so
    this additive batch cannot change server-mode behaviour. The duplication is
    accepted deliberately: server byte-identity beats DRY here, and ops-3d-3 may
    converge the two only if it can be shown byte-identical against the
    equivalence fixtures.
    """
    grounding = await _grounding_fetch(
        text,
        attached_images=attached_images,
        callbacks=_HostCallbacks(choose, ask_text, progress),
        jira_content_json=jira_content_json,
    )
    if isinstance(grounding, str):
        return grounding
    await _grounding_gates(
        proceed_anyway=proceed_anyway,
        attached_images=attached_images,
        audit_source=audit_source,
    )
    return grounding


@dataclass
class PreparePayloadResult:
    """Structured result of handle_prepare_test_cases.

    Exactly one of (payload + prep_id) or clarify is populated:
    * payload / prep_id: the grounded host-mode payload + its store handle.
    * clarify: a markdown string to relay verbatim (setup hint, preflight,
      clarifying questions, or an error) -- no suite was prepared.

    ``notice`` carries any NON-SILENT disclosure. It is written ONCE, on the
    success path (_host_mode_server_llm_notice); a further disclosure must be
    APPENDED, never assigned, or it will silently drop that one. The image-drop
    disclosure is emitted separately, as its own text block by
    assemble_prepare_payload. ``images`` (raw {data, mime} dicts) is
    populated in ops-3d-2 when the MCP tool result can carry image content.
    ops-3d-2/3d-3 render this into MCP content; render_prepare_payload turns it
    into plain text for a legacy (string-only) caller.
    """

    payload: dict | None = None
    prep_id: str = ""
    clarify: str = ""
    notice: str = ""
    images: list = field(default_factory=list)


def _host_mode_server_llm_notice(
    *,
    ac_boomeranged: bool = False,
    img_boomeranged: bool = False,
    checklist_boomeranged: bool = False,
    rule_packs_narrowed: bool = False,
) -> str:
    """Disclose what this server did NOT do on the host path.

    ALWAYS returns at least one line -- the ambiguity-preflight disclosure is
    unconditional -- so there is no "nothing to disclose" early return any
    more. Never raises.

    2026-08-14 (flag-surface reduction, batch 8b-ii): the "these settings
    still make THIS SERVER call an LLM" paragraph, its `on` list and the
    `_boomeranged` set that filtered it are GONE, together with the four
    risk / test-plan / NLI-tier / comment-reconciliation blocks and their
    parameters. All six settings were DELETED and hardcoded, five of them
    OFF, so each block's text -- "`QA_LLM_RISK_SCORING` is on",
    "`QA_TEST_PLAN_ARTIFACTS` is on", "`QA_COMMENT_RECONCILE_ENABLED` is on
    and this ticket has comments" -- asserted a fact that can no longer be
    true. Announcing a deleted setting as ON is the same over-claim class
    this function was built to prevent, inverted. The `on` list was provably
    empty in any case: five members are permanently False and the sixth,
    the checklist, is unconditionally filtered out as boomeranged.

    The four parameters below are RETAINED because they still vary per
    prepare: a ticket carrying real ACs ships no AC job, a prepare with no
    screenshots ships no image job, and rule_packs_narrowed is the only
    channel by which a REVIVED rule pack's narrowing reaches the tester.
    """
    lines = _notice_base_blocks(ac_boomeranged, img_boomeranged)
    lines += _notice_extra_blocks(checklist_boomeranged, rule_packs_narrowed)
    return "\n".join(lines)


def _notice_base_blocks(ac_boomeranged: bool, img_boomeranged: bool) -> list:
    """Pre-pass, acceptance-criteria and image disclosure lines."""
    lines: list = []
    # Host-side ambiguity preflight, UNCONDITIONAL: the TICKET-7154 pre-pass
    # never runs server-side, because llm.resolve_generation_mode() returns
    # the "host" constant, so _ground_and_gate gets run_ambiguity_llm=False.
    lines.append(
        "> \u2139\ufe0f  The requirement pre-pass did **not** run on "
        "this server: the under-specified/no-UI check is handed to your own "
        "chat model instead. That is why this prepare made no "
        "ambiguity-gate LLM call."
    )
    if ac_boomeranged:
        lines.append(
            "> \u2139\ufe0f  This ticket carries no acceptance criteria, and this "
            "server did **not** synthesize any: deriving them is step 0b of the "
            "payload's `jobs_to_run`. Return them as a top-level "
            "`acceptance_criteria` array with your suite; they will be labelled "
            "MODEL-DERIVED, and without them the suite finalizes with no "
            "requirements traceability."
        )
    if img_boomeranged:
        lines.append(
            "> \u2139\ufe0f  This server made **no** vision call for the "
            "screenshot(s): they are attached to this reply as image content for "
            "your OWN multimodal model. This server has no vision backend to "
            "call and needs no key of its own. Read them as step 0c of the "
            "payload's `jobs_to_run`, ground "
            "your cases in them, and return an optional top-level "
            "`image_descriptions` array so this server can record what they "
            "showed."
        )
    return lines


def _notice_extra_blocks(
    checklist_boomeranged: bool, rule_packs_narrowed: bool
) -> list:
    """Checklist-fold and rule-pack narrowing disclosure lines."""
    lines: list = []
    # Residue R4: the checklist fold's own block. It names the fold, the
    # return field, BOTH submission routes and the honest cost (host
    # authorship of the coverage denominator), plus the two server-side
    # counterweights that are what make the fold defensible rather than an
    # over-claim. 2026-08-14 (batch 8b-ii): it no longer opens by naming
    # QA_ATOMIC_CHECKLIST_ENABLED -- that setting was DELETED and the
    # checklist hardcoded ON, so announcing it as a live switch would be the
    # over-claim this whole function exists to prevent, inverted.
    if checklist_boomeranged:
        lines.append(
            "> \u2139\ufe0f  This server made **no** "
            "requirement-decomposition call: breaking the "
            "ticket into an atomic requirements checklist is step 0d of the "
            "payload's `jobs_to_run`. Derive it BEFORE you generate, generate "
            "so that every item is covered, and return it as an optional "
            "top-level `checklist_items` array with your merged suite (on the "
            "per-category route, in the finalize review sidecar). This server "
            "assigns every `CL-NNN` id and labels the result MODEL-DERIVED. "
            "**You will have authored both the requirement set and the test "
            "cases.** This server's pure-Python granularity audit still runs "
            "over your list and is the only independent check left. Omit the "
            "field and the suite finalizes with no requirement checklist at "
            "all: nothing is decomposed to fill the gap."
        )
    # ...and the ONE accepted capability narrowing that rides with it, claimed
    # only when a rule pack actually mandated a line that is now falling back to
    # prompt + advisory instead of being scored.
    if rule_packs_narrowed:
        lines.append(
            "> \u2139\ufe0f  Rule packs (bilingual / atomicity / standing "
            "rules) mandated requirement "
            "line(s) for this ticket, and with the decomposition handed to you "
            "they run in **prompt + advisory** mode: the mandated lines still "
            "reach your generation prompt and the advisory rule-pack report is "
            "still rendered, but they are NOT interleaved into the checklist, "
            "so nothing checks them against the suite. Cover them anyway -- nothing "
            "downstream will tell you if you did not."
        )
    return lines


async def _audit_image_plan_nudge(plan: str, channel: str, missing_ids) -> None:
    """Audit + log ONE image-plan completion nudge exit. Never raises.

    2026-08-10 (I1): both nudge returns in handle_prepare_test_cases handed the
    tester a clarify and left NOTHING behind -- no audit row, no log line -- so
    the repeating "how many screens?" loop observed that day (09:04, ~09:20,
    13:03:48) was invisible in telemetry, while every other exit on that path
    (image gate beat 1, beat 2, the _prepare_generation refusal) was traceable.

    A NEW event id rather than a `mcp_prepare_rejected` variant: that row means
    "_prepare_generation refused this source", a different fact, and overloading
    it would make both unqueryable. `missing_ids` are HOST-supplied capture ids,
    so they are length- and count-capped here even though `_peek_captures`
    already produced and deduped them.
    """
    try:
        all_ids = list(missing_ids or [])
        ids = [str(x)[:64] for x in all_ids][: capture._MAX_MISSING_SHOWN]
        # The COUNT is the real total; the id LIST is the capped sample. The
        # row used to write len(ids) -- the capped number -- as
        # `unresolved_captures`, so a 27-id miss read as 8 in telemetry
        # (round-6 review): a message must describe the artifact the reader
        # gets, on the audit side as much as the tester's.
        unresolved = len(all_ids)
        safe_plan = str(plan or "")[:32]
        await _common._audit(
            "mcp_image_plan_nudge",
            detail={
                "plan": safe_plan,
                "channel": channel,
                "unresolved_captures": unresolved,
                "capture_ids": ids,
                "capture_ids_truncated": unresolved > len(ids),
            },
        )
        logger.info(
            "image-plan nudge: plan=%s channel=%s unresolved=%d",
            safe_plan,
            channel,
            unresolved,
        )
    except Exception:  # pragma: no cover - a disclosure never breaks a prepare
        logger.debug("image-plan nudge audit failed", exc_info=True)


def _ago_label(seconds) -> str:
    """Tester-facing age for a notice: "less than a minute", "12 minute(s)",
    "2 hour(s) 31 minute(s)". Never raises.

    I3 (2026-08-10): the duplicate-SUITE window is a day wide now, so the old
    bare "{n} minute(s)" would routinely have printed "151 minute(s) ago".
    """
    try:
        total = max(0, int(seconds))
    except Exception:
        return "some time"
    mins = total // 60
    if mins < 60:
        return f"{mins} minute(s)" if mins else "less than a minute"
    hours, rem = divmod(mins, 60)
    return f"{hours} hour(s) {rem} minute(s)" if rem else f"{hours} hour(s)"


def _safe_snapshot_stamp(raw) -> str:
    """A Jira `fields.updated` value as safe, single-line, capped display text.

    Already sanitized by `jira_mcp._sanitize_echo` on the boomerang path; capped
    again here because this module must not depend on WHERE a grounded dict came
    from. Set/str-based like every other sanitizer in this file -- `re` is
    deliberately not imported at module scope. Never raises.
    """
    try:
        text = "".join(ch for ch in str(raw or "") if ch.isprintable() and ch != "`")
        return " ".join(text.split())[:40]
    except Exception:
        return ""


def _parse_jira_timestamp(raw):
    """Epoch seconds for a Jira timestamp, or None when it cannot be read.

    None means "say nothing": these strings are HOST-supplied, so an unparseable
    one must never produce a warning -- and never an exception. Handles the two
    forms Jira emits that `datetime.fromisoformat` alone does not: a trailing
    `Z`, and the compact `+0300` offset. A naive stamp is read as UTC, which is
    only ever used to compare two stamps from the same ticket.
    """
    try:
        from datetime import datetime, timezone

        text = str(raw or "").strip()
        if not text:
            return None
        if text[-1] in ("Z", "z"):
            text = text[:-1] + "+00:00"
        if len(text) > 5 and text[-5] in "+-" and text[-3] != ":":
            text = text[:-2] + ":" + text[-2:]
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


async def _stale_snapshot_note(source_text: str, updated: str) -> str:
    """WARN when the ticket snapshot just handed back is OLDER than one this
    install already prepared from. "" (silence) in every other case.

    2026-08-10 (I2c). The host caches `jira_content_json` on disk and re-sends
    hours-old copies -- files written at 09:06 were re-sent at 13:06 -- and the
    server had no recency signal at all. Deliberately a WARNING and never a
    refusal: the timestamps are host-supplied text, so a hard gate would be both
    gameable and annoying.

    Reads the newest prior prep for the SAME source inside QA_PREP_TTL_S that
    stamped a `jira_updated`. FINALIZED preps count here, unlike the
    duplicate-prep guard, because a finished generation is exactly the snapshot
    a later call must be compared against. Never raises.
    """
    if not source_text or not updated:
        return ""
    try:
        hit = await prep_store.find_prep_snapshot_by_source(
            source_text, float(getattr(settings, "qa_prep_ttl_s", 86400) or 0)
        )
        prior = (hit or {}).get("content") or {}
        prior_stamp = _safe_snapshot_stamp(prior.get("jira_updated"))
        if not prior_stamp or prior_stamp == updated:
            return ""
        new_t = _parse_jira_timestamp(updated)
        old_t = _parse_jira_timestamp(prior_stamp)
        if new_t is None or old_t is None or new_t >= old_t:
            return ""
        when = time.strftime(
            "%H:%M", time.localtime(float(prior.get("created_at") or 0))
        )
        return (
            "> \u26a0\ufe0f The ticket snapshot you just sent (`updated` = "
            f"`{updated}`) is OLDER than the one this install already prepared "
            f"from at {when} (`{prior_stamp}`), so this is almost certainly a "
            "CACHED copy of the ticket rather than a fresh read. Re-run "
            "`getJiraIssue` and prepare again if the ticket may have changed. "
            "Generation continues with the snapshot you sent."
        )
    except Exception:
        logger.debug("stale-snapshot check failed", exc_info=True)
        return ""


def _jira_content_chars(url_content) -> int:
    """How much TICKET TEXT this grounded payload yielded. 0 when unknown.

    F9 (2026-08-15). Counts the two fields that actually ground generation --
    the description and the acceptance criteria -- and deliberately NOT
    ``raw_text``, which has the comment thread appended: comments arrive
    through a different path and a ticket gaining a comment between two runs
    would otherwise read as the ticket growing. Never raises.
    """
    try:
        if not isinstance(url_content, dict) or url_content.get("error"):
            return 0
        return len(str(url_content.get("description") or "")) + len(
            str(url_content.get("acceptance_criteria") or "")
        )
    except Exception:  # pragma: no cover - defensive
        return 0


# F9 thresholds. A payload must be BOTH proportionally and absolutely smaller
# before anything is said, so neither a long ticket losing a sentence nor a
# two-line ticket losing a word can trip it.
#
# MEASURED, not guessed: the reported incident is 4,262 chars against 8,600+ for
# the same ticket -- a ratio of 0.50 and a delta of 4,338 -- so 0.80 / 500 sits
# an order of magnitude inside the real signal while leaving room for the
# genuinely benign differences the SAME revision can still produce (a client
# that renders an ADF table with different cell padding, or normalises
# whitespace).
_CONDENSED_MAX_RATIO = 0.80
_CONDENSED_MIN_DELTA = 500


async def _condensed_payload_note(source_text: str, updated: str, chars: int) -> str:
    """WARN when the SAME ticket revision came back materially SMALLER than a
    prior run of this install already saw. "" in every other case.

    F9 (2026-08-15). ``tools/jira_mcp``'s fetch directive tells the host "Do NOT
    summarise, reword, translate or truncate it", and a host ignored that: it
    handed back a condensed ticket (4,262 stored chars against 8,600+ for the
    same ticket on another run) and the server had no way to tell. It showed up
    downstream as the DF04 status matrix collapsing to ``Received`` alone,
    losing the Shipped and Delivered rows that two other requirements depend on.

    WHY THIS COMPARISON AND NOT A DIRECT ONE: this server never fetches the
    ticket. ``jira_mcp`` returns a fetch DIRECTIVE and the host's own Atlassian
    MCP connection does the read, so there is no server-side original to diff
    against -- which is exactly why asking the host to confirm its own fidelity
    is worthless. ``fields.updated`` is the lever: it is Jira's own revision
    stamp, so two payloads carrying the SAME ``updated`` value describe
    BYTE-IDENTICAL ticket content by definition. Any material size difference
    between them was introduced by whatever sat between Jira and this server. No
    model is asked anything; this is arithmetic on two integers.

    KNOWN LIMIT, stated rather than papered over: it needs a PRIOR prep for the
    same ticket revision, so it cannot fire on the first run of a ticket. Every
    prep now stamps its size, so the baseline exists from the first run onward
    -- which is precisely the shape of the reported incident, where the same
    ticket was prepared more than once. A first-run condensation still passes
    silently; detecting THAT would need a second independent read of the ticket.

    Deliberately a WARNING and never a refusal: it is a size heuristic over
    host-supplied text, and the honest response to "this looks truncated" is to
    tell the tester, not to destroy the run. Never raises.
    """
    if not source_text or not updated or chars <= 0:
        return ""
    try:
        hit = await prep_store.find_prep_snapshot_by_source(
            source_text, float(getattr(settings, "qa_prep_ttl_s", 86400) or 0)
        )
        prior = (hit or {}).get("content") or {}
        return _condensed_note_from_prior(prior, chars, updated)
    except Exception:
        logger.debug("condensed-payload check failed", exc_info=True)
        return ""


def _condensed_note_from_prior(prior: dict, chars: int, updated: str) -> str:
    """The CONDENSED warning when *prior* (a stored prep) shows a material
    shrink for the SAME revision, else ""."""
    prior_chars = int(prior.get("content_chars") or 0)
    prior_stamp = _safe_snapshot_stamp(prior.get("jira_updated"))
    # SAME revision only. A different `updated` means the ticket itself
    # changed, and a genuinely shortened ticket is not a defect.
    if not prior_stamp or prior_stamp != updated or prior_chars <= 0:
        return ""
    if chars > prior_chars * _CONDENSED_MAX_RATIO:
        return ""
    if (prior_chars - chars) < _CONDENSED_MIN_DELTA:
        return ""
    when = time.strftime("%H:%M", time.localtime(float(prior.get("created_at") or 0)))
    return _condensed_payload_warning(chars, updated, prior_chars, when)


def _condensed_payload_warning(
    chars: int, updated: str, prior_chars: int, when: str
) -> str:
    """The CONDENSED-ticket warning for *chars* now against *prior_chars* at *when*."""
    pct = int(round(100 - (chars * 100.0 / prior_chars)))
    return (
        "> \u26a0\ufe0f  **The ticket text looks CONDENSED.** This payload "
        f"carries {chars:,} characters of description and acceptance "
        f"criteria; the same ticket revision (`updated` = `{updated}`) "
        f"yielded {prior_chars:,} at {when} on this install -- about {pct}% "
        "less text for content Jira says is unchanged, so the difference "
        "was introduced between Jira and here. The `getJiraIssue` result "
        "must be passed through RAW: do not summarise, reword, translate or "
        "truncate it, and do not re-render it as prose. Re-run the fetch "
        "and prepare again. Generation continues with what you sent, but "
        "requirements that only exist in the missing text -- table rows, "
        "status lists, edge cases -- cannot be covered."
    )


def _image_gate_would_fire(
    text: str,
    plan: str,
    jira_content_json: str,
    attached_images: object,
    attested: object,
) -> bool:
    """THE condition for the Jira image gate -- one definition, two callers.

    2026-08-31: this lived as an inline `if` at the gate, mirrored BY HAND
    inside _pending_image_gate_hint. The copies drifted three times in one day:
    a `.strip()` added on the gate side only (C10), an empty-entry filter added
    on the gate side only (C5), and `capture_ids` counted as screens by the hint
    when the gate counts only the ones that RESOLVE. Each drift silenced the
    hint on a call the gate then stopped. A parity test was tried as the
    mitigation and pinned two of the drifts as correct, which is what settled
    the argument for extracting this.

    Callers pass ALREADY-normalized values -- `plan` is "" when unusable,
    `attested` an int, `attached_images` the merged, empty-filtered list -- so
    this never has to guess what shape it was handed. Whitespace is not a
    ticket: `"   "` is truthy and used to suppress the pre-fetch ask on what is
    still a first call. Never raises.
    """
    try:
        return bool(
            not plan
            and not str(jira_content_json or "").strip()
            and not attached_images
            and not attested
            and image_gate._gate_jira_source(text)
        )
    except Exception:  # pragma: no cover - defensive; a gate never breaks a prepare
        logger.debug("_image_gate_would_fire failed", exc_info=True)
        return False


def _supplied_images_for_hint(attached_images: object, capture_ids: object) -> list:
    """The screens the image gate WILL see, for _pending_image_gate_hint."""
    # Normalize EXACTLY as the gate does before asking, or the predicate is
    # shared in name only:
    #   * drop empty entries (C5) -- [None] / [b""] / [""] is not a screen;
    #   * resolve captures the way the gate WILL see them. This is the
    #     subtle one and it has now been wrong in both directions.
    #     _peek_captures reads the TRAY only, but the gate runs after
    #     _revive_captures has pulled re-sent ids back off the carry-forward
    #     SHELF -- so a shelved id (the documented "re-send the SAME
    #     capture_ids" path, which _carry_forward_or_refuse also injects
    #     into capture_ids) looked unresolvable here and resolvable there:
    #     the hint promised a screens question that never came. That FALSE
    #     PROMISE is worse than the false negative it replaced, because a
    #     tester acts on it. _resolvable_captures answers tray-OR-shelf
    #     without moving anything, which is what this pre-clarify position
    #     requires.
    supplied = [i for i in (attached_images or []) if i]
    if not supplied and capture_ids:
        supplied = list(capture._resolvable_captures(list(capture_ids)))
    return supplied


def _pending_image_gate_hint(
    text: str,
    source_plan: str,
    jira_content_json: str,
    attached_images: object,
    attached_image_count: object,
    capture_ids: object,
) -> str:
    """A heads-up that the Jira image gate is STILL AHEAD, for appending to a
    duplicate-prep / duplicate-suite clarify.

    Both duplicate guards deliberately run BEFORE the image gate -- an
    already-open prep is the cheaper, more urgent signal -- but that ordering
    MASKED the gate: a tester re-running a ticket got the duplicate warning and
    never the screens question, so the gate only appeared after a retry with
    `proceed_anyway=true`, costing a further round trip (found in the
    2026-08-31 live sweep, where it hid every Jira use case). This keeps the
    ordering and removes the masking.

    Returns "" whenever the gate would not fire anyway, so a non-Jira source, a
    caller that already stated a plan, and one that already supplied screens are
    never told about a question they will not be asked. It does NOT re-derive
    that condition -- it normalizes its arguments the way the gate does and asks
    _image_gate_would_fire, because the hand-written mirror this replaced
    drifted three times in a day. Never raises."""
    try:
        try:
            attested = max(0, int(attached_image_count or 0))
        except (TypeError, ValueError, OverflowError):
            attested = 0
        supplied = _supplied_images_for_hint(attached_images, capture_ids)
        if not _image_gate_would_fire(
            text,
            image_gate._normalize_source_plan(source_plan),
            jira_content_json,
            supplied,
            attested,
        ):
            return ""
        return (
            "\n\n> \U0001f4f8 **Heads-up: I have not asked about this ticket's "
            "SCREENS yet.** That question comes next, so a bare retry spends "
            "another round trip on it. If the user has already said where the "
            "screens come from, send `source_plan` on the SAME retry -- `jira` "
            "(ticket text only), `jira_attach`, `jira_device`, `jira_both` or "
            "`device`. Do NOT guess it if they have not said."
        )
    except Exception:  # pragma: no cover - a hint never breaks a prepare
        logger.debug("_pending_image_gate_hint failed", exc_info=True)
        return ""


def _jira_page_without_issue_note(text: str) -> str:
    """Refusal when *text* is a URL on a Jira HOST that names no ISSUE, else "".

    PURE: no DNS lookup, no fetch, no settings write -- exactly the host/path
    test ``tools.jira_fetcher.fetch_url_content`` already makes, Confluence
    (``/wiki/``) exemption included, because a published space really does
    serve readable HTML.

    F22 (2026-09-02 audit): that refusal lives PAST the Jira image gate, so a
    tester who pasted a board URL was first asked "how do I get this ticket's
    screens?" -- for a ticket that does not exist -- answered it, and only then
    learned the URL was unusable. Shape first. Never raises.
    """
    try:
        candidate = (text or "").strip()
        if not candidate.lower().startswith(("http://", "https://")):
            return ""
        from tools.jira_mcp import is_jira_host, jira_page_without_issue_message

        if looks_like_jira_url(candidate):
            return ""
        if not is_jira_host(candidate):
            return ""
        if "/wiki/" in (urlparse(candidate).path or ""):
            return ""
        return jira_page_without_issue_message(candidate)
    except Exception:
        logger.debug("_jira_page_without_issue_note failed", exc_info=True)
        return ""


def _bare_issue_key_refusal(text: str) -> str:
    """Refusal when *text* is a BARE issue key and nothing else, else "".

    2026-09-02 audit F8. A bare issue key is not a feature description. It used
    to be handled as one, and generated a full suite whose every case was about
    the literal string "TICKET-7154" -- grounded in nothing, traceable to
    nothing, and indistinguishable from a real suite in the reply. Route it to
    the same Atlassian fetch directive a ticket URL gets, by resolving it
    against JIRA_BASE_URL; when no base URL is configured, ask for the link.
    The grammar matches the WHOLE input only, so a description that merely
    cites a ticket still generates from the tester's own words.

    Round-2 review (M1): this DISCLOSES rather than silently rewriting the
    input. A one-token input is ambiguous by nature -- `T-1000` could be a
    ticket or a feature name -- and naming the resolution costs one turn and
    makes it correctable in that turn. Never raises.
    """
    _bare_key = bare_issue_key(text)
    if not _bare_key:
        return ""
    _bare_url = issue_url_for_key(_bare_key)
    if _bare_url:
        logger.info("prepare: bare issue key %s read as a ticket", _bare_key)
        return (
            f"⚠️ `{_bare_key}` looks like a Jira issue key, not "
            "a feature description -- and generating from the key alone "
            "would invent a suite about those characters rather than "
            "about the ticket. On this install that key is "
            f"<{_bare_url}>. Re-send THAT url and I'll read the ticket. "
            f"If `{_bare_key}` is really the name of the feature and not "
            "a ticket, describe the feature in a sentence or two and "
            "I'll work from that instead."
        )
    return (
        f"⚠️ `{_bare_key}` looks like a Jira issue key, not "
        "a feature description -- and generating from the key alone "
        "would invent a suite about that text rather than about the "
        "ticket. This install has no `JIRA_BASE_URL` configured, so I "
        "can't turn the key into a link myself. Send the full ticket "
        "URL instead, or paste the ticket's text and I'll work from "
        "that."
    )


def _prepare_input_refusal(
    text: str, jira_content_json: str, stage_token: str
) -> tuple[str, str]:
    """``(refusal, jira_content_json)`` for the input-only refusals of a prepare.

    Empty input, an unresolvable stage token, a bare issue key. Reads no
    store and moves no screen, so a refusal here returns before anything is
    touched. ``refusal`` is "" when the call may proceed.

    v1.97.0 cursor-hardening (item 1): ``jira_content_json`` is assembled from
    the per-fetch stage tray when the caller sent ``stage_token`` instead. An
    explicitly-supplied ``jira_content_json`` still wins (legacy callers keep
    working unchanged). Never raises.
    """
    if not text:
        return (
            "Tell me what to build test cases for -- a feature description, a "
            "Jira/issue URL, a web page URL, or a Swagger/OpenAPI spec URL."
        ), jira_content_json
    jira_content_json, stage_err = jira._resolve_jira_content_json(
        jira_content_json, stage_token
    )
    if stage_err:
        return stage_err, jira_content_json
    return _bare_issue_key_refusal(text), jira_content_json


@dataclass
class _PreflightCarry:
    """What the pre-flight resolved about re-sent and carried-forward screens."""

    capture_ids: list | None
    carried_ids: list
    carry_note: str
    carried_from: str = ""


def _attested_image_count(attached_image_count: object, attached_images: object) -> int:
    """This call's ATTESTED screens: the host's own count plus any image bytes
    a caller handed over directly (the Feature-Analysis route does).

    2026-08-09 (review H1): only the attested channel is assembled here; the
    CAPTURED channel is re-resolved inside _carry_forward_or_refuse. A coercion
    failure fails OPEN on this channel -- 99 attested means no attested gap and
    no refusal from it -- because a refusal must never be triggered by a bug in
    its own precondition. Never raises.
    """
    try:
        return image_gate._clamped_count(attached_image_count, hi=99) + len(
            [i for i in list(attached_images or []) if i]
        )
    except Exception:  # pragma: no cover - a coercion never breaks a prepare
        logger.debug("incoming-image check failed", exc_info=True)
        return 99


async def _find_open_prep(text: str) -> dict | None:
    """A recent unfinalized prep for this exact source, or None.

    2026-08-03: the duplicate guard used to query only finished suites. A real
    run made two preps 43s apart for a byte-identical source, was told nothing,
    and discarded a whole preparation. Never raises.
    """
    _window = max(0, int(getattr(settings, "qa_host_duplicate_prep_window_s", 1800)))
    try:
        _recent = await prep_store.find_recent_prep_by_source(text, _window)
        return (_recent or {}).get("content")
    except Exception:
        logger.debug("recent-prep duplicate check failed", exc_info=True)
        return None


def _merge_carry_forward(
    prep: dict, carry: _PreflightCarry, *, image_carry_ack: bool, have_attested: int
) -> str:
    """Fold _carry_forward_or_refuse's verdict on *prep* into *carry*; the
    image-loss refusal text, or "".

    2026-08-09: losing the previous prep's screens is the silent harm the
    duplicate guard exists to prevent. Unlike the open-prep clarify this runs
    REGARDLESS of `proceed_anyway` and takes its own `image_carry_ack=true`.
    The helper compares the prior prep's counts against this call's, credits
    any cross-channel surplus, and returns all-empty when nothing went missing.
    It revives BEFORE its refusal, deliberately (see "Deferred REVIVE").
    """
    _ids, _more_carried, _from, _note, _refusal = capture._carry_forward_or_refuse(
        prep,
        image_carry_ack=image_carry_ack,
        capture_ids=carry.capture_ids,
        have_attested=have_attested,
    )
    if _ids:
        carry.capture_ids = list(_ids)
    if _more_carried:
        # APPEND: ids revived from a re-sent list and ids recovered from
        # the prior prep are different findings and both are stamped.
        carry.carried_ids = list(carry.carried_ids) + [
            c for c in _more_carried if c not in carry.carried_ids
        ]
        carry.carried_from = _from
    if _note:
        carry.carry_note = (
            (carry.carry_note + "\n\n" + _note) if carry.carry_note else _note
        )
    return _refusal or ""


async def _prepare_preflight(
    text: str,
    capture_ids: list | None,
    attached_images: list | None,
    attached_image_count: int,
    image_carry_ack: bool,
) -> tuple[str, _PreflightCarry, dict | None]:
    """``(refusal, carry, open_prep)``: the re-sent-id probe and the image half
    of the duplicate-prep guard. ``refusal`` is "" when the call may proceed.

    RE-SENT ids first (2026-08-09): after a prepare ships them they sit on the
    carry-forward shelf, not in the tray. This is a PROBE (review M3): it
    reports what WOULD revive and mutates nothing, because a dismissible
    clarify can still end this call; the real revive is committed by the
    caller once the call is certain to proceed. The open-prep clarify itself
    is the caller's, because it is dismissible by `proceed_anyway`.
    """
    capture_ids, _carried_ids, _carry_note = capture._revive_resent_captures(
        capture_ids
    )
    carry = _PreflightCarry(capture_ids, _carried_ids, _carry_note)
    _have_attested = _attested_image_count(attached_image_count, attached_images)
    _prep = await _find_open_prep(text)
    if not _prep:
        return "", carry, None
    _refusal = _merge_carry_forward(
        _prep, carry, image_carry_ack=image_carry_ack, have_attested=_have_attested
    )
    if _refusal:
        return _refusal, carry, _prep
    if carry.carried_ids and not carry.carried_from:
        # Re-sent ids revived above: attribute them to the prep they came
        # from, so the stamp and the audit row say where the screens began.
        carry.carried_from = str(_prep.get("prep_id") or "")
    return "", carry, _prep


def _open_prep_refusal(prep: dict) -> str:
    """The dismissible "a prep for this source is ALREADY open" clarify."""
    _mins = max(0, int(float(prep.get("age_s") or 0) / 60))
    _ago = f"{_mins} minute(s)" if _mins else "less than a minute"
    return (
        "⚠️ A preparation for this exact source is ALREADY open "
        f"(`{prep.get('prep_id', '?')}`, started {_ago} ago) and has "
        "not been finalized. Preparing again starts a SECOND full "
        "generation of the same ticket -- 8 categories of cases your "
        "chat model has to write twice -- and does not continue or "
        "replace the open one.\n\n"
        "To CONTINUE the open one, submit its categories against that "
        "prep_id (`qa_prep_status` shows what is still missing). To "
        "deliberately start over, call `qa_prepare_test_cases` again "
        "with `proceed_anyway=true`."
    )


def _prepare_reply_notes(notice: str, *notes: str) -> str:
    """*notice* with every non-empty note APPENDED in order, blank-line apart.

    APPEND, never assign -- see PreparePayloadResult."""
    for note in notes:
        if note:
            notice = (notice + "\n\n" + note) if notice else note
    return notice


def _ticket_image_note(url_content: dict, attested: int, captured: int) -> str:
    """What the reply must say about the ticket's own screens, or "".

    The Atlassian MCP server returns attachment METADATA only, so ticket
    screenshots cannot ride along as image content; surface that by NAME
    instead of silently generating a suite that never saw them.
    """
    if url_content.get("images_unavailable"):
        # Batch C, C1: partial-fetch aware; "" means nothing to add.
        return image_gate._unreadable_images_note(
            url_content, attested=attested, captured=captured
        )
    if url_content.get("description_image_refs") and not url_content.get(
        "images_fetched_server_side"
    ):
        # Ahead of attachments_unknown on purpose: when the description
        # embeds images we KNOW the ticket has them. Labels are already
        # charset-gated and capped by jira_mcp._image_ref_labels.
        _n = int(url_content.get("description_image_refs") or 0)
        _img_labels = [
            str(x).strip()
            for x in (url_content.get("description_image_labels") or [])[:8]
            if str(x).strip()
        ]
        _label_note = (
            " The ticket labels them: " + ", ".join(f"`{x}`" for x in _img_labels) + "."
            if _img_labels
            else ""
        )
        return (
            "> ℹ️ This ticket's description embeds "
            f"{_n} image(s) — UI mockups or screens — that I could "
            "NOT read: Jira is read through your own Atlassian MCP "
            "connection, which returns text, not image bytes. The cases below "
            "come from the ticket TEXT only — attach those screens to "
            f"this chat and I'll read them.{_label_note}"
        )
    if url_content.get("attachments_unknown"):
        # NOT the same as "no attachments": the payload never carried the
        # field, so we cannot tell.
        return (
            "> ℹ️ I could not tell whether this ticket has "
            "screenshots — the Jira payload came back without the "
            "`attachment` field. If the ticket has UI images, attach them to "
            "this chat and I'll read them; otherwise the cases below are from "
            "the ticket TEXT only."
        )
    return ""


def _captured_screens_note(cap_labels: list, img_job: bool) -> str:
    """Captured device screens, NAMED -- or, with no image job, the warning
    that they went nowhere (no server-side vision path since P2-F1).

    Tester-typed labels are UNTRUSTED text, so they ride inside wrap_untrusted.
    """
    if not cap_labels:
        return ""
    if img_job:
        return (
            "> 📸 Captured device screens, in the order they are attached to "
            "this reply:\n\n"
            + wrap_untrusted("captured_screen_labels", "\n".join(cap_labels), limit=800)
        )
    return (
        f"> ⚠️ {len(cap_labels)} captured device screen(s) were NOT "
        "forwarded to your model as image content. No image content rides "
        "on this reply, and this server describes no screenshots itself, "
        "so nothing they show is reflected in the cases below."
    )


def _snapshot_note(snapshot_updated: str) -> str:
    """I2b (2026-08-10): WHICH ticket snapshot the cases came from, or ""."""
    if not snapshot_updated:
        return ""
    return (
        f"> 🕒 Ticket snapshot as of `{snapshot_updated}` (the "
        "`updated` timestamp on the Jira payload you handed back). This "
        "server never fetches the ticket itself, so if that looks old, "
        "re-run `getJiraIssue` before generating again."
    )


def _missing_captures_note(missing_count: int, rendered: str) -> str:
    """The capture ids that contributed NO screen, or "".

    *rendered* is the caller's ``_render_missing`` of its own
    ``_peek_captures`` list (pinned by tests/test_render_missing_pairing.py).
    """
    if not missing_count:
        return ""
    return (
        f"> ⚠️ {missing_count} capture id(s) contributed NO screen "
        f"({rendered}). "
        "An id with no "
        "reason beside it "
        "is unknown or expired; one with a reason was pushed out by a "
        "later capture or did not fit this call. Re-run "
        "`qa_capture_screens` if those screens matter."
    )


class _ImageIntake(NamedTuple):
    """The attachments after empty-drop and capture merge, plus both counts."""

    attached_images: list | None
    attested: int
    captured: int


class _GateCtx(NamedTuple):
    """What both image-gate beats read: the source, the ticket and the images."""

    text: str
    jira_content_json: str
    attached_images: list | None
    attested: int
    captured: int


class _Beat1(NamedTuple):
    """Beat 1's outcome: the plan to proceed with, or the menu to return."""

    plan: str
    menu: str | None


class _TextOnlySkip(NamedTuple):
    """The text-only image-skip stamp: how many ticket screens, and their names."""

    count: int
    names: list


class _TicketNotes(NamedTuple):
    """The ticket recency and fidelity notes, computed before the envelope."""

    snapshot_updated: str
    stale: str
    recent: str
    content_chars: int
    condensed: str


class _ImageFlags(NamedTuple):
    """The host image-job stamps: job, relevance, preflight and require."""

    job: bool
    relevance: bool
    preflight: bool
    require: bool


class _PrepRun(NamedTuple):
    """Everything the envelope, payload, audit and reply of one prepare read."""

    text: str
    grounded: _Grounding
    prepared: Any
    capture_ids: list | None
    cap_labels: list
    image_carry_ack: bool
    carry: _PreflightCarry
    intake: _ImageIntake
    skip: _TextOnlySkip
    notes: _TicketNotes
    img: _ImageFlags
    host_amb: bool
    ac_job: bool
    checklist_job: bool
    rp_narrowed: bool
    prospective_images: list
    captured_shipped: int


# This block sits BEFORE the handler's protective `try:` (it must: the gate
# has to run ahead of _ground_and_gate, which is what fetches), and this
# handler's docstring promises it never raises -- mcp_server._tracked
# re-raises, so an exception here would surface as an MCP tool error. The
# helpers above are all never-raising; the two coercions that touch
# host-supplied values therefore guard themselves.
#
# 2026-08-31 (C5): drop empty entries, on EVERY call. A host that sends
# [{}] or [b""] supplied NO screen, but a non-empty list silently
# satisfied the beat-1 guard below and skipped the disclosure entirely.
# This deliberately sits OUTSIDE the `if _cap_images:` branch: the first
# cut put it inside, where it only ran when capture ids were also
# present -- i.e. never on the path the defect actually took. The gate's
# own pin caught that, which is the whole reason it exists.
# `or None` preserves the pre-existing None-vs-[] distinction for every
# downstream reader, so filtering cannot change a call that had nothing
# to filter.
#
# Captured device screens join the chat attachments, so they flow
# through _ground_and_gate -> host_images -> _select_prepare_images
# and inherit the existing byte budget and drop disclosure with no
# new capping code.
#
# 2026-08-09: DEVICE-captured screens are the SECOND intake channel and were
# counted NOWHERE. Only the chat-ATTESTED count was stamped, so a
# capture-only run stamped attached_image_count=0 while host_image_job=true,
# and _attested_image_gap_note's strong "there is NO evidence any image was
# actually read" warning was STRUCTURALLY UNREACHABLE -- exactly the
# TICKET-5646 run. Counted here, beside the attested channel, and stamped
# below. _peek_captures returns only tray entries it actually resolved (an
# unknown or expired id lands in _cap_missing and is named in the reply), so
# this cannot count a screen that does not exist.
# WHAT IT MEANS, precisely (review finding M4): screens HANDED to the chat
# client on this payload. The per-result byte/count budget runs later, in
# _select_prepare_images, which may still drop some -- and NAMES every one it
# drops. So the disclosure wording deliberately says "handed ... any screen
# dropped for size is named in the prepare reply" rather than asserting the
# host received all N.
def _image_intake(
    attached_images: list | None, cap_images: list, attached_image_count: int
) -> _ImageIntake:
    """Drop empty attachments, merge captured screens, count both channels."""
    try:
        attached_images = [i for i in (attached_images or []) if i] or None
        if cap_images:
            attached_images = list(attached_images or [])
            attached_images = attached_images + cap_images
    except Exception:
        logger.debug("merging captured screens failed", exc_info=True)
        attached_images = cap_images or None
    try:
        _attested = max(0, int(attached_image_count or 0))
    except (TypeError, ValueError, OverflowError):
        _attested = 0
    try:
        _captured = len([i for i in (cap_images or []) if i])
    except Exception:  # pragma: no cover - a count never breaks a prepare
        logger.debug("counting captured screens failed", exc_info=True)
        _captured = 0
    return _ImageIntake(attached_images, _attested, _captured)


# --- Jira image gate, BEAT 1 (QA_IMAGE_GATE_ENABLED) ------------------- #
# Placed AFTER the duplicate-prep guard (an already-open prep is the
# cheaper, more urgent signal) and BEFORE the fetch
#
# 2026-08-31 (F3): that ordering is still right, but it MASKED this gate
# -- a tester re-running a ticket saw only the duplicate warning and never
# the screens question, which then cost a further round trip after the
# proceed_anyway retry. Both duplicate clarifies now append
# _pending_image_gate_hint, whose condition MIRRORS the `if` below. Change
# one and you must change the other, or the hint promises a question that
# never comes (or stays silent about one that does). -- which is the whole
# point: the "I cannot read Jira images" disclosure used to be appended to a
# payload the host had ALREADY been told to generate from.
#
# REACHABILITY: handle_generate_test_cases re-routes into this handler in
# host mode, so both beats fire on qa_generate_test_cases too -- which is
# why that handler and that tool forward these same four arguments.
#
# Jira sources only, FIRST call only. A plain feature / web / Swagger
# source skips this entirely, and a caller that already stated a plan or
# already supplied images is never asked twice -- `attached_images`
# counts, which is what keeps the Feature-Analysis `jira_mobile` route
# (it captures screens FIRST, then reaches this handler through the
# host-mode reroute) from being asked where its screens come from. NO prep is saved on a
# gated round, so the duplicate-prep guard sees nothing new on the
# follow-up call and a re-ask can never start a second full generation.
#
# "elicit" (audit detail):
# 2026-08-09 (FIX 3): {"resolved": false, "plan": ""} did not say
# WHY, so a client that cannot show an elicitation, a tester who
# declined, and an install with QA_MCP_ELICIT_ENABLED off all read
# identically. "<enum>/<text>" over the two tiers, with
# "disabled/disabled" for the flag-off / no-callback case --
# "unavailable/unavailable" is the genuine client-capability limit,
# which is what the 2026-08-09 run actually was.
#
# FIX 3 (review H4): the status decides HOW the fallback explains
# itself. It must not assert a client limitation when elicitation was
# simply turned off on this server, or when the tester declined.
#
# 2026-08-31 (finding 1): beat 1 was skipped on ATTESTATION ALONE -- a
# count with no bytes behind it and no plan. The chat attachments stay
# in the host's context by design, so this server can never verify the
# number; what it can do is stop the skip being invisible. The honest
# bypass (`image_gate_ack=true`) is disclosed in the finished payload,
# so the unverifiable one should not be quieter than the honest one.
async def _image_gate_beat1(
    ctx: _GateCtx,
    plan: str,
    source_plan: str,
    choose: _common.ChooseCb,
    ask_text: _elicit.AskCb,
) -> _Beat1:
    """Jira image gate beat 1: elicit the source plan, or audit an attested skip."""
    if _image_gate_would_fire(
        ctx.text,
        plan,
        ctx.jira_content_json,
        ctx.attached_images,
        ctx.attested,
    ):
        _picked, _elicit_status = await image_gate._elicit_source_plan_status(
            choose, ask_text
        )
        await _common._audit(
            "mcp_image_gate_beat1",
            detail={
                "resolved": bool(_picked),
                "plan": _picked,
                "elicit": _elicit_status,
            },
        )
        if not _picked:
            _md = image_gate._image_gate_menu_markdown(_elicit_status, source_plan)
            return _Beat1(plan, _md)
        return _Beat1(_picked, None)
    elif (
        not plan
        and not str(ctx.jira_content_json or "").strip()
        and not ctx.attached_images
        and ctx.attested
        and image_gate._gate_jira_source(ctx.text)
    ):
        await _common._audit(
            "mcp_image_gate_beat1",
            detail={
                "resolved": False,
                "plan": "",
                "skipped": True,
                "skip_reason": "attested_without_bytes",
                "attested": ctx.attested,
            },
        )
    return _Beat1(plan, None)


def _attach_nudge_text(plan: str) -> str:
    """The "Attach the screenshots now" nudge, plus the BOTH-channels sentence."""
    _both = (
        " You chose BOTH channels, so in the same call also pass the "
        "`capture_ids` returned by `qa_capture_screens`."
        if plan == "jira_both"
        else ""
    )
    return (
        "## 📎 Attach the screenshots now\n\n"
        "Ask the user to attach the screen(s) to THIS chat (as many "
        "as they have), then call the SAME tool again with the SAME "
        "`feature_or_url`, `source_plan='" + plan + "'` and "
        "`attached_image_count=<how many they attached>`. If this "
        "call already carried `jira_content_json`, re-send that SAME "
        "value -- do NOT fetch the ticket again. The images stay in "
        "YOUR context -- no image bytes are sent to this server -- "
        "and the generation payload will ask you to describe them "
        "before you write the cases. If those screens are not "
        "available after all, call again with `image_gate_ack=true` "
        "and I will generate from the ticket text alone and say so "
        "in the reply." + _both
    )


def _capture_nudge_text(plan: str) -> str:
    """The "Capture the screens first" nudge for a device plan without captures."""
    return (
        "## 📸 Capture the screens first\n\n"
        "Call `qa_capture_screens` (it picks the device, offers a "
        "Rescan, and captures screen after screen), then call the "
        "SAME tool again with the SAME `feature_or_url`, "
        "`source_plan='" + plan + "'` and the `capture_ids` it "
        "returns -- plus, if this call already carried "
        "`jira_content_json`, that SAME value again (do NOT fetch "
        "the ticket a second time). Those ids survive the Jira fetch "
        "directive and any failed attempt, so they can be re-sent "
        "unchanged. If no device is reachable, call again with "
        "`image_gate_ack=true` instead and I will generate from the "
        "ticket text alone and say so in the reply. Nothing has been "
        "prepared yet, so this costs no generation."
    )


# Plan-completion nudge: a plan that PROMISED images and has not
# delivered them yet gets ONE actionable instruction per missing channel,
# on every call until it is satisfied. `image_gate_ack=true` is always
# the way out, so this can never dead-end. Still no fetch, no prep.
#
# Attach nudge:
# I1 (2026-08-10): this exit is tester-visible and used to leave no
# trace at all, so a loop of them was invisible in telemetry.
#
# Capture nudge:
# I1 (2026-08-10): same finding as the attach nudge above. The
# unresolved-id count is the useful signal here -- it separates
# "never captured anything" from "sent ids that expired".
async def _image_plan_nudge(
    plan: str, ctx: _GateCtx, cap_images: list, cap_missing: list
) -> str | None:
    """The audited nudge for the first channel the plan has not delivered, or None."""
    if plan in ("jira_attach", "jira_both") and not ctx.attested:
        await _audit_image_plan_nudge(plan, "attach", cap_missing)
        return _attach_nudge_text(plan)
    if plan in ("jira_device", "jira_both", "device") and not cap_images:
        await _audit_image_plan_nudge(plan, "capture", cap_missing)
        return _capture_nudge_text(plan)
    return None


# --- Jira image gate, BEAT 2: the INFORMED ask -------------------- #
# The ticket is in hand now, so this one can NAME the screens. Fires
# ONLY when the ticket revealed images and nothing supplied them;
# SILENT otherwise, so a plan that already covered the screens is never
# asked twice. Runs BEFORE _prepare_generation, so a gated round costs
# no enrichment, no prep row and no generation -- and because no prep is
# saved, the duplicate-prep guard cannot fire on the follow-up call.
# The captures are still in the tray (peeked, not popped), so re-sending
# the same capture_ids works.
# P0-3: runs whatever `image_gate_ack` says; the ack now only opens
# the tester confirmation dialog below.
#
# N4 (2026-08-10): bound, not inlined -- the audit row could not tell
# "no screens at all" from "3 of 4 arrived" without the ratio.
# 2026-08-31 (C2): NOT a sum. `attached_images` already contains the
# device captures (merged above) and, on hosts that forward them, the
# SAME chat screens `_attested` counts -- so summing double-counted
# one channel and let 2-of-3 read as 3-of-3, re-opening the exact
# subset-generation hole the completeness rule was added to close.
# Chat bytes vs chat attestation: take the larger, they are one
# channel. Device captures are a genuinely separate channel and add.
# RESIDUAL, accepted: a host that folds its device captures INTO
# attached_image_count over-counts by the overlap. Unfixable here --
# the count is an unverifiable host claim either way (see the
# attested_without_bytes audit stamp below) -- and it is a much
# narrower hole than the one it replaces, which needed no
# misreporting at all to fire.
#
# v1.97.0 cursor-hardening (item 2): text-only source_plan='jira'
# clears beat 2 in ONE ask now (see _image_gate_second_beat above),
# but the skip must still be disclosed, never silent -- stamped into
# envelope["meta"] below and rendered at submit time by
# _text_only_image_skip_note.
#
# P0-3: the ack is the AGENT's claim that the tester agreed; only
# the tester's own pick in this dialog clears beat 2. No dialog,
# a dismissal or the attach option keep it shut.
#
# K2 (2026-08-10): INSIDE the gated branch on purpose. At the
# _ticket_image_evidence call above, _beat2 is not decided yet, so
# shelving there would stamp labels even when beat 2 stays SILENT
# (images already supplied) and a later unrelated capture would pop
# them. This branch is the only path that actually sends the tester
# off to capture.
# N3b's fetch-failure disclosure went with the fetch (batch D1)
# and its dead renderer (2026-09-02); a revived fetch adds its
# own note here.
async def _image_gate_beat2(
    grounded: _Grounding,
    ctx: _GateCtx,
    plan: str,
    image_gate_ack: bool,
    choose: _common.ChooseCb,
) -> PreparePayloadResult | _TextOnlySkip:
    """Jira image gate beat 2: the informed ask, or the text-only skip stamp."""
    _img_n, _img_names, _img_kind = image_gate._ticket_image_evidence(
        grounded.url_content
    )
    _chat_side = max(0, len(ctx.attached_images or []) - ctx.captured)
    _have_images = max(_chat_side, ctx.attested) + ctx.captured
    _beat2 = image_gate._image_gate_second_beat(
        count=_img_n,
        names=_img_names,
        kind=_img_kind,
        plan=plan,
        have_images=_have_images,
    )
    _text_only_image_skip_count, _text_only_image_skip_names = (
        image_gate._text_only_image_skip_stamp(plan, _img_n, _img_names)
    )
    if _beat2 and image_gate_ack:
        _beat2 = await image_gate._confirm_image_gate_skip(choose, _beat2)
    if _beat2:
        capture._shelve_ticket_image_labels(_img_names, ctx.text)
        await _common._audit(
            "mcp_image_gate_beat2",
            detail={
                "kind": _img_kind,
                "count": _img_n,
                "plan": plan,
                "have_images": _have_images,
            },
        )
        return PreparePayloadResult(clarify=_beat2)
    return _TextOnlySkip(_text_only_image_skip_count, _text_only_image_skip_names)


# I2 (2026-08-10): ticket snapshot RECENCY. The host caches the Jira
# payload on disk and re-sends hours-old copies, and `fields.updated` is
# the only recency signal obtainable WITHOUT a second fetch -- the
# directive now asks for the field and jira_mcp echoes it through. Both
# values are computed HERE, before the envelope is written, so the stamp
# below and the disclosure further down can never disagree. Empty for a
# non-Jira source, or a host that trimmed the field.
#
# v1.97.0 cursor-hardening (item 8): a suite already exists for this
# exact source and is still within the warn window -- WARN only,
# never blocks generation. Restores what _find_recent_duplicate_suite
# gave up in 1.96.0 (only the 30s _RECENT_CALLS replay cache survived
# that cut).
#
# F9: and the fidelity half of the same question. _stale_snapshot_note
# asks "is this the CURRENT revision of the ticket?"; this asks "is this
# the WHOLE of it?" -- the failure the fetch directive's "do NOT
# summarise, reword or truncate" was already trying to prevent, with
# nothing behind it. Same lookup, so no extra store round trip beyond
# the one already made.
async def _ticket_recency_notes(text: str, grounded: _Grounding) -> _TicketNotes:
    """The ticket's stale-snapshot, recent-suite and condensed-payload notes."""
    _snapshot_updated = _safe_snapshot_stamp(
        (grounded.url_content or {}).get("updated")
    )
    _stale_note = await _stale_snapshot_note(text, _snapshot_updated)
    _recent_suite_note = await _recent_suite_warning_note(
        text, _RECENT_SUITE_WARN_WINDOW_S
    )
    _content_chars = _jira_content_chars(grounded.url_content)
    _condensed_note = await _condensed_payload_note(
        text, _snapshot_updated, _content_chars
    )
    return _TicketNotes(
        _snapshot_updated,
        _stale_note,
        _recent_suite_note,
        _content_chars,
        _condensed_note,
    )


# Pop the DEFERRED Tier-3 screenshot BEFORE _prepare_generation: raw
# bytes must never reach serialize_prepared / the prep store, which are
# JSON. The key is absent unless Tier 2 actually rendered a screenshot
# that yielded no elements, so this is a no-op on every other page.
# Everything the host's own multimodal model should see. Jira ticket
# images are the pre-existing forwarding (still key-gated inside
# jira_fetcher._fetch_jira_images, so a keyless install has none); the
# chat attachments and the page screenshot need NO key at all, which is
# what makes this useful on a keyless host-mode deployment.
def _host_forward_images(
    grounded: _Grounding, attached_images: list | None, host_img: bool
) -> list:
    """Pop the deferred screenshot, then list the images the host should see."""
    page_screenshot = None
    if isinstance(grounded.ui_content, dict):
        page_screenshot = grounded.ui_content.pop("vision_screenshot", None)
    host_images: list = []
    if host_img:
        host_images = [
            i for i in ((grounded.url_content or {}).get("images") or []) if i
        ]
        host_images += [i for i in (attached_images or []) if i]
        if page_screenshot:
            host_images.append(
                {
                    "filename": "rendered_page.png",
                    "mime": "image/png",
                    "data": page_screenshot,
                }
            )
    return host_images


# Narrower than the flag, exactly like _ac_job: with nothing to forward
# there is no job to ship and nothing to ask the host for.
# Widened for the host-ATTESTED chat-attachment channel: with
# attached_image_count > 0 the images live in the HOST's own context and
# no bytes reached this server, so host_images is empty -- but IMAGE_JOB
# must still ship, because its returned image_descriptions[] is the ONLY
# verification tell that the attested images were actually read.
#
# QA_IMAGE_RELEVANCE_ENABLED: ask the SAME job for a per-image
# relevance verdict (agents.host_image.IMAGE_RELEVANCE_JOB -- zero extra
# round trips, no server-side LLM call, and NO change to step 0c's
# grounding instruction). Narrower than the flag, exactly like _img_job
# itself: with no job shipped there is nothing to ask for. Decided and
# stamped HERE, at prepare time, so a mid-flow .env flip or a launcher
# auto-update cannot change what an in-flight prep expects back.
#
# Batch 4 LAYER 1 (QA_HOST_IMAGE_PREFLIGHT_ENABLED, default ON): ask the
# SAME job to ACT on its own `no` verdict in the host's PARENT turn --
# stop and ask the tester -- instead of only reporting it beside a suite
# that has already been generated from the wrong screen. Narrower than
# the flag, exactly like _img_relevance itself: with no verdict
# requested there is nothing to act on. Decided and stamped HERE so a
# mid-flow .env flip or a launcher auto-update cannot change what an
# in-flight prep was told to do.
#
# Batch 4 LAYER 2 (QA_HOST_IMAGE_REQUIRE_RELEVANT, default OFF): whether
# a submission whose screens came back `no` -- or with no usable verdict
# at all -- is REFUSED at finalize. Same stamp-not-live discipline and
# the same narrowing: a prep that never asked for a verdict can never be
# judged on one. The submit side additionally requires that screens were
# actually FORWARDED on this prep (captured or chat-attested), so a
# ticket-image-only prep is never enforced.
def _image_job_flags(host_img: bool, host_images: list, attested: int) -> _ImageFlags:
    """Decide the image job and its relevance/preflight/require stamps."""
    _img_job = bool(host_img and (host_images or attested))
    _img_relevance = bool(_img_job)
    _img_preflight = bool(_img_relevance)
    _img_require = bool(
        _img_relevance and getattr(settings, "qa_host_image_require_relevant", False)
    )
    return _ImageFlags(
        job=_img_job,
        relevance=_img_relevance,
        preflight=_img_preflight,
        require=_img_require,
    )


# Phase 3a's two POST_MERGE folds -- the `_risk_job` / `_plan_job`
# locals, both hardcoded False since 2026-08-14 (batch 8b-ii) -- were
# DELETED on 2026-08-16 (dead-code deletion P2-H) together with
# host_mode.RISK_JOB / TEST_PLAN_JOB, their prep-meta stamps, the
# Path-A sidecar copy and the submit-side extraction. Nothing is
# boomeranged in their place and nothing calls a model: risk scoring is
# the deterministic score_and_sort heuristic, and there are no
# test-plan artifacts. Reviving either is a fresh implementation -- see
# docs/LLM_MIGRATION_INVENTORY.md rows 10 and 12.
# Residue R4 (ledger id `atomic_checklist.decompose`). This USED to be
# the last server-side LLM call on the prepare path, and it was decided
# here because it was an ARGUMENT to _prepare_generation
# (`decompose_checklist`). Dead-code deletion P2-F2 deleted
# tools/atomic_checklist.decompose_to_checklist and that parameter on
# 2026-08-16, so the decision now governs ONE thing: whether
# CHECKLIST_JOB ships to the host. 2026-08-14 (batch 8b-ii):
# QA_ATOMIC_CHECKLIST_ENABLED was DELETED and hardcoded ON, so this is
# True on every install and the job ships on every host prepare.
# Reads the SEAM, not a literal, so tests/conftest.py's suite-wide pin
# governs it and a revival is one line.
def _checklist_job_on() -> bool:
    """Whether CHECKLIST_JOB ships to the host on this prepare."""
    import llm
    from tools.atomic_checklist import checklist_enabled

    return bool(checklist_enabled() and llm.resolve_generation_mode() == "host")


# Early return from _prepare_generation (unreadable source / no real
# feature text) -- its first element is the tester-facing message,
# which already NAMES what was missing or unreadable, so it is passed
# through unchanged rather than re-worded on top of itself.
#
# N2 (2026-08-10): this refusal was the ONE prepare outcome that left
# no audit row at all, so a tester reporting "it just asked me a
# question again" was untraceable. Capped, single-line, machine-safe.
async def _run_prepare_generation(
    text: str,
    grounded: _Grounding,
    attached_images: list | None,
    progress: _common.ProgressCb,
    capture_ids: list | None,
) -> PreparePayloadResult | Any:
    """Run _prepare_generation; an early-return tuple becomes an audited clarify."""

    async def _on_status(msg: str) -> None:
        await _common._emit(progress, msg)

    prepared = await _prepare_generation(
        text,
        grounded.url_content,
        grounded.ui_content,
        attached_images=attached_images,
        openapi_text=grounded.openapi_text,
        on_status=_on_status,
    )
    if isinstance(prepared, tuple):
        _reject_msg = str(prepared[0] or "")
        await _common._audit(
            "mcp_prepare_rejected",
            detail={
                "reason": " ".join(_reject_msg.split())[:120],
                "source_kind": "url" if _common._is_url(text) else "text",
            },
        )
        return PreparePayloadResult(
            clarify=_reject_msg + capture._capture_retry_hint(capture_ids)
        )
    return prepared


# Residue R4: the ONE capability narrowing this fold accepts, and the
# TESTER -- not only the ledger and docs/FEATURE_FLAGS.md -- has to be
# told about it. agents/test_scenario_agent.py interleaves the Batch-3
# MANDATED rule-pack lines into the checklist and sets
# rule_packs.checklist_mode ONLY when the checklist is already
# non-empty; with the decomposition boomeranged it is empty at that
# line, so the packs fall back to PROMPT + ADVISORY mode -- the mandated
# lines still reach the generator and the advisory report still renders,
# but nothing checks them against the suite. Disclosed ONLY when a pack actually
# mandated a line AND the fallback really happened: announcing a
# narrowing that could not have occurred is the same over-claim class
# the _boomeranged set exists to prevent. Never raises -- a disclosure
# that cannot be computed must not break a prepare.
def _rule_pack_narrowed(prepared: Any, checklist_job: bool) -> bool:
    """Whether a mandated rule pack fell back to prompt + advisory mode."""
    _rp_narrowed = False
    if checklist_job:
        try:
            from tools.rule_packs import rule_pack_checklist_items

            _rp = getattr(prepared, "rule_packs", None)
            _rp_narrowed = bool(
                _rp is not None
                and rule_pack_checklist_items(_rp)
                and not getattr(_rp, "checklist_mode", False)
            )
        except Exception:  # pragma: no cover - advisory disclosure only
            logger.debug("rule-pack narrowing check failed", exc_info=True)
    return _rp_narrowed


# 2026-08-09 (review M1): the images that will ACTUALLY ride on this
# reply, resolved BEFORE the envelope so the stamp can record what SHIPS
# rather than what was read off the tray. Reused verbatim as
# `ticket_images` below -- one expression, so the budget the stamp
# simulates and the budget the reply applies cannot drift.
def _prospective_images(img_job: bool, host_images: list, grounded: _Grounding) -> list:
    """The images that will actually ride on this prepare reply."""
    return (
        list(host_images)
        if img_job
        else list((grounded.url_content or {}).get("images") or [])
    )


# "image_reference_advisory":
# A6 (2026-09-26, v1.98 scope A): _screen_reference_advisory
# already computes this in tools/jira_mcp.py -- one hit
# repo-wide before this plan, the assignment, nothing read
# it. Stamped here so it survives to finalize (Step 5's
# Generation Notes row) and to the prepare reply below.
#
# "app_version":
# ops-6 (bug 1): the launcher applies updates "at the next idle
# minute", and a host-mode flow is idle exactly between prepare
# and submit -- so a restart lands MID-FLOW and the envelope is
# deserialized by a different code version than wrote it.
# Observed on 2026-07-29. Stamp the writer so submit can say so.
#
# "code_fingerprint":
# F4 (2026-08-15): the version string is NOT enough. A developer
# checkout reports the same 0.1.0 across every code change, and
# data/suites.db is shared by every server process on the
# machine, so a prep staged by pre-fix code is silently reused
# by a fixed server -- which is how a fixed _strip_html still
# produced placeholder-stripped output on the TICKET-5645 run.
# A content hash of the prep-shaping modules moves whenever they
# do. Disclosure only; "" when unavailable.
#
# "host_ambiguity_review":
# 2026-07-30 evening: stamp when prepare skipped the server
# classifier so a mid-flow flag flip remains auditable.
#
# "host_ac_job":
# Stamped at PREPARE time for the same reason: submit must know
# whether to expect an `acceptance_criteria` field, and a
# mid-flow .env flip must not change that for an in-flight prep.
#
# "host_image_job":
# Stamped at PREPARE time for the same reason: submit must know
# whether to expect an `image_descriptions` field, and a mid-flow
# .env flip must not change that for an in-flight prep.
#
# "text_only_image_skip_count":
# v1.97.0 cursor-hardening (item 2): non-zero only when beat 2
# was skipped because the tester picked source_plan='jira'
# (ticket text only) while the ticket itself referenced
# screens -- read by _text_only_image_skip_note so the skip is
# disclosed, never silent.
#
# "attached_image_count":
# Host-ATTESTED chat attachments (no image bytes ever reached
# this server). Stamped for the same mid-flow-flip reason: the
# submit reply compares it against the returned
# image_descriptions and SAYS SO when a count was attested but
# nothing came back.
#
# "captured_image_count":
# DEVICE-captured screens HANDED to the chat client on this
# payload -- the server's own observation, unlike the attested
# channel above, which it has no evidence for at all. Stamped for
# the same mid-flow-flip reason and read by
# _attested_image_gap_note, so a capture-only run can reach that
# disclosure at all. NOT a claim that all N survived the
# _select_prepare_images byte/count budget: that runs later and
# names anything it drops.
# 2026-08-09 (review M1): the SHIPPED count. Stamping the count
# read off the tray meant a 5-capture prepare stamped 5 while
# _select_prepare_images capped the reply at jira_max_images (3),
# so _attested_image_gap_note fired a permanent, false "only 3 of
# 5" whose advice ("supply them again and prepare again") could
# never be satisfied. Anything the budget drops is still NAMED in
# the prepare reply by _select_prepare_images itself.
#
# THE OTHER READERS, deliberate and accepted (review W1). This
# stamp has two more consumers and BOTH want "shipped":
#   * _image_relevance_refusal's FORWARDED-SCREENS GUARD sums
#     this and attached_image_count to decide whether there was
#     anything for the host to judge. A capture the byte/count
#     budget dropped never reached the host's model, so a
#     relevance verdict for it could not exist and enforcing one
#     would be a pure false positive. The behaviour change is
#     therefore intended: an all-dropped-captures prep with no
#     attested images now opts that gate out, exactly as a prep
#     with no images at all does.
#   * prep_store's carry-forward hit reports it as the prior
#     prep's captured PROMISE, and only shipped screens ever
#     grounded that prep, so shipped is the honest promise.
# `captured_image_read` keeps the pre-budget count beside it for
# forensics; nothing gates on it.
#
# "capture_ids":
# 2026-08-09 (re-prepare carry-forward): the capture ids this
# prep shipped and their tester-typed labels. IDS ONLY -- never
# bytes, which must not enter the JSON prep store -- so a
# RE-PREPARE of the same source inside the duplicate-prep window
# can revive the exact screens off _CARRY_SHELF, and the refusal
# can NAME what would otherwise vanish. Host-supplied strings, so
# both the count and each id's LENGTH are capped before they are
# persisted; tiny against QA_PREP_MAX_BYTES either way.
#
# "carried_forward_capture_count":
# Carry-forward provenance, stamped for the same mid-flow-flip
# reason as every field here: which prep these screens came from,
# how many were revived, and whether the tester ACKed generating
# without them.
def _meta_head(run: _PrepRun) -> dict:
    """Prep-meta keys 1-19, source_text through image_carry_ack, in order."""
    return {
        "source_text": run.text,
        "source_url": run.text if run.grounded.url_content else "",
        "image_reference_advisory": str(
            (run.grounded.url_content or {}).get("image_reference_advisory") or ""
        ),
        "round": 0,
        "app_version": _common._BOOT_VERSION,
        "code_fingerprint": code_fingerprint(),
        "host_ambiguity_review": bool(run.host_amb),
        "host_ac_job": bool(run.ac_job),
        "host_image_job": bool(run.img.job),
        "text_only_image_skip_count": run.skip.count,
        "text_only_image_skip_names": run.skip.names,
        "attached_image_count": run.intake.attested,
        "captured_image_count": run.captured_shipped,
        "captured_image_read": run.intake.captured,
        "capture_ids": [str(c)[:64] for c in (run.capture_ids or [])][:24],
        "captured_image_labels": [str(x) for x in (run.cap_labels or [])][:8],
        "carried_forward_capture_count": len(run.carry.carried_ids),
        "carried_forward_from_prep": run.carry.carried_from,
        "image_carry_ack": bool(run.image_carry_ack),
    }


# Whether THIS prep asked IMAGE_JOB for a per-image relevance
# verdict. Submit reads this stamp, never the live flag, so an
# OLD envelope (no stamp) parses no verdict and warns about
# nothing.
#
# Batch 4: whether THIS prep asked the host to STOP AND ASK the
# tester before generating from a screen it judged off-topic
# (Layer 1), and whether an off-topic or unjudged submission is
# REFUSED at finalize (Layer 2). Same stamp-not-live rule as
# every field above -- submit reads these stamps, never the live
# flags -- so an OLD envelope carries neither key and its submit
# is byte-identical to today's.
#
# Residue R4: whether THIS prep handed the requirement
# decomposition to the host, so submit knows whether to expect
# a `checklist_items` field. Same mid-flow-flip rule as above:
# submit reads this stamp, never the live flag.
#
# I2 (2026-08-10): the ticket's own `fields.updated` for THIS
# prep. Stamped so a LATER prepare for the same source can tell
# that the payload it was handed is older than the one already
# used -- the only way to see a re-sent cached snapshot without
# fetching the ticket a second time. Additive and .get-read
# everywhere, so an envelope written before this key existed is
# simply "no prior stamp" and stays silent.
#
# F9 (2026-08-15): how much ticket text THIS revision yielded,
# so a later prepare for the same revision can tell that it was
# handed less. Stamped unconditionally -- the baseline is only
# useful if every run writes one -- and read with .get, so an
# envelope written before this key existed is simply "no
# baseline" and the check stays silent.
#
# Parallel fan-out contract is stamped at PREPARE time so a mid-flight
# .env flip cannot change the finalize gate for an in-flight prep.
# 2026-08-09 (Batch 3, FIX 1): whether THIS prep warns on a
# duplicate qa_submit_category and REFUSES a shrinking one.
# Stamped at PREPARE time for the same mid-flow-flip reason as
# every stamp above -- submit reads these stamps, never the live
# flags -- so an .env flip or a launcher auto-update between
# prepare and submit cannot change an in-flight prep. An OLD
# envelope carries neither key, so its REPLY is byte-identical to
# today (its audit row still gains prior_cases/replaced on a
# re-submission -- see the audit block in handle_submit_category:
# forensic fidelity is deliberately not gated on a disclosure
# flag, review M1).
#
# Batch 1 (2026-08-09): the generation-VOLUME contract,
# stamped for the same mid-flow-flip reason as every field
# above. The payload tells the host `min_cases` per category
# (host_mode.build_prepare_payload) and NOTHING on the submit
# side ever checked it: the 08-09 08:23 run finalized 8 cases
# -- one per category against a floor of 8 -- 28 seconds after
# prepare, silently, where two comparable runs produced 99 and
# 97 with no category below 12. `volume_min_cases` MUST be
# stamped: prepared.categories is (name, focus,
# preferred_type) with no counts, so submit cannot re-derive
# it. `volume_categories` is stamped UNCONDITIONALLY -- unlike
# `expected_categories`, which stays fan-out-only -- because
# the floor is checked on BOTH finalize routes, including a
# merged submit for a prep that never asked for the fan-out.
# The live flag is read exactly ONCE, here.
def _meta_stamps(run: _PrepRun) -> dict:
    """Prep meta keys 20-32: image/checklist jobs, Jira recency, fan-out, volume."""
    return {
        "host_image_relevance": bool(run.img.relevance),
        "host_image_preflight": bool(run.img.preflight),
        "host_image_require_relevant": bool(run.img.require),
        "host_checklist_job": bool(run.checklist_job),
        "jira_updated": run.notes.snapshot_updated,
        "jira_content_chars": run.notes.content_chars,
        "host_category_resubmit_note": True,
        "host_category_shrink_guard": True,
        "parallel_fanout": bool(_parallel_fanout_on()),
        "expected_categories": (
            expected_category_names(run.prepared) if _parallel_fanout_on() else []
        ),
        "volume_floor": True,
        "volume_min_cases": prepared_case_bounds(run.prepared)[0],
        "volume_categories": expected_category_names(run.prepared),
    }


async def _save_prep_or_refuse(
    envelope: dict, capture_ids: list | None
) -> str | PreparePayloadResult:
    """Save the envelope: the new prep_id, or the "Could not stage" clarify."""
    saved = await prep_store.save_prep(envelope, created_by="qa_prepare_test_cases")
    prep_id = (saved.get("content") or {}).get("prep_id") or ""
    if saved.get("error") or not prep_id:
        return PreparePayloadResult(
            clarify=(
                "⚠️ Could not stage the prepared generation: "
                f"{saved.get('error') or 'unknown store error'}"
                + capture._capture_retry_hint(capture_ids)
            )
        )
    return prep_id


def _build_prepare_payload(run: _PrepRun, prep_id: str) -> dict:
    """The host payload for this prep, with the ambiguity and step-zero jobs."""
    payload = build_prepare_payload(run.prepared, prep_id)
    if run.host_amb:
        payload = attach_ambiguity_job(payload)
    # The GENERAL job mechanism. Also indexes the ambiguity job attached
    # just above (host_mode._LEGACY_JOB_KEYS), and is a no-op returning a
    # key-identical payload when neither is on.
    _host_jobs = (
        ([AC_JOB] if run.ac_job else [])
        + (
            [
                IMAGE_PREFLIGHT_JOB
                if run.img.preflight
                else (IMAGE_RELEVANCE_JOB if run.img.relevance else IMAGE_JOB)
            ]
            if run.img.job
            else []
        )
        + ([CHECKLIST_JOB] if run.checklist_job else [])
    )
    payload = attach_jobs(payload, _host_jobs)
    return payload


# F3 (2026-08-30): the step-zero jobs live on the PAYLOAD, which is
# built AFTER save_prep and never persisted -- so
# `qa_get_category_job(prep_id, "all")`, the one call this server's own
# `instructions` block tells a host to make, returned the 8 category
# jobs and NONE of the step-zero ones, including the ambiguity preflight
# marked `blocking: true` and the acceptance-criteria job whose ac_ids
# every case's requirement_id must cite. A host following the
# instructions literally therefore discovered the AC job at FINALIZE,
# by which time all 95 cases were staged with requirement_id null and
# the only remedy was regenerating the suite (measured 2026-08-30: 0 of
# 95 traced following the instructions, 79 of 79 running step 0 first).
# Persist the step-zero subset so that call can serve it. Best-effort:
# a failed update costs the passthrough, never the prep.
async def _persist_step_zero(payload: dict, envelope: dict, prep_id: str) -> None:
    """Best-effort: persist the payload's step-zero jobs onto the saved envelope."""
    try:
        _sz_index = [
            _e
            for _e in (payload.get("jobs_to_run") or [])
            if isinstance(_e, dict) and str(_e.get("stage") or "") == "step_zero"
        ]
        if _sz_index:
            _sz: dict = {"jobs_to_run": _sz_index}
            for _e in _sz_index:
                _k = str(_e.get("payload_key") or "")
                if _k and isinstance(payload.get(_k), dict):
                    _sz[_k] = payload[_k]
            envelope["step_zero"] = _sz
            await prep_store.update_prep(prep_id, envelope)
    except Exception:  # pragma: no cover - never costs the prepare
        logger.debug("step-zero passthrough persist failed", exc_info=True)


# Phase 3a: tell the notice which server-side calls THIS prepare handed
# to the chat. Since 2026-08-14 (batch 8b-ii) the notice names no
# setting at all -- all six were deleted -- so it takes only the four
# booleans that still vary from one prepare to the next.
#
# Item 2b: disclose any OTHER in-flight prep (fetched worker packets /
# staged rows) so an interrupted run is resumable instead of silently
# evaporating. APPEND, never assign -- see PreparePayloadResult.
# The Atlassian MCP server returns attachment METADATA only, so ticket
# screenshots can no longer ride along as image content (tools/jira_mcp
# sets images_unavailable when the ticket HAD images it could not carry).
# Surface that to the tester by NAME instead of silently generating a
# suite that never saw the screenshots. APPEND, never assign.
#
# The server-fetched-screens note went with the fetch (batch D1) and
# its dead renderer (2026-09-02); only the unreadable-images note below
# is still a fact this server can state.
#
# Reading order: ticket screens, captured screens, WHAT the cases are
# grounded on (always), the A6 screen-reference advisory (evidence
# about the payload, so regardless of which gate branch fired), the
# I2b ticket snapshot, staleness, a recent suite (v1.97.0 item 8), F9
# condensing, the revive / carry-forward disclosure, then capture ids
# that contributed no screen.
async def _prepare_reply_notice(
    run: _PrepRun, prep_id: str, missing_note: Callable[[], str]
) -> str:
    """The prepare reply's notice: server-call note, then every disclosure."""
    _notice = _host_mode_server_llm_notice(
        ac_boomeranged=run.ac_job,
        img_boomeranged=run.img.job,
        checklist_boomeranged=run.checklist_job,
        rule_packs_narrowed=run.rp_narrowed,
    )
    _url_content = run.grounded.url_content or {}
    _screen_ref_note = str(_url_content.get("image_reference_advisory") or "")
    _notice = _prepare_reply_notes(
        _notice,
        _ticket_image_note(_url_content, run.intake.attested, run.captured_shipped),
        _captured_screens_note(run.cap_labels, run.img.job),
        image_gate._grounding_source_note(
            run.text, _url_content, run.grounded.openapi_text
        ),
        ("> ℹ️ " + _screen_ref_note) if _screen_ref_note else "",
        _snapshot_note(run.notes.snapshot_updated),
        run.notes.stale,
        run.notes.recent,
        run.notes.condensed,
        run.carry.carry_note,
        missing_note(),
    )
    _unfinished = await prep_status._unfinished_preps_note(exclude_prep_id=prep_id)
    _notice = _prepare_reply_notes(_notice, _unfinished)
    return _notice


# Item 6: carry the RAW ticket screenshots so the TOOL layer can forward
# them to the host's OWN multimodal model as MCP image content. This
# server makes no vision call at all -- P2-F1 deleted the last two on
# 2026-08-16. Present only when JIRA_FETCH_IMAGES + ANTHROPIC_API_KEY let
# jira_fetcher download them; otherwise empty and the payload's text
# image_context is the fallback. Bytes are never persisted in the prep store.
# QA_HOST_IMAGE_DESCRIPTION_ENABLED additionally forwards the tester's
# chat attachments and any deferred Tier-3 page screenshot. OFF (or with
# nothing extra to send) this is EXACTLY today's ticket-images-only list.
# _select_prepare_images still applies the byte budget and discloses any
# image it has to drop, so the wider list needs no new cap here.
async def _finish_prepare(
    run: _PrepRun, missing_note: Callable[[], str]
) -> PreparePayloadResult:
    """Stage the envelope, build the payload, audit, and ship the reply."""
    serialized = serialize_prepared(run.prepared)
    envelope = {
        "prepared": serialized,
        "meta": {**_meta_head(run), **_meta_stamps(run)},
    }
    prep_id = await _save_prep_or_refuse(envelope, run.capture_ids)
    if isinstance(prep_id, PreparePayloadResult):
        return prep_id
    payload = _build_prepare_payload(run, prep_id)
    await _persist_step_zero(payload, envelope, prep_id)
    await _common._audit(
        "mcp_prepare_test_cases",
        entity_id=prep_id,
        detail={
            "host_ambiguity_review": bool(run.host_amb),
            "host_ac_job": bool(run.ac_job),
            "host_image_job": bool(run.img.job),
            "host_image_relevance": bool(run.img.relevance),
            "host_image_preflight": bool(run.img.preflight),
            "host_image_require_relevant": bool(run.img.require),
            "captured_image_count": run.captured_shipped,
            "captured_image_read": run.intake.captured,
            "carried_forward_capture_count": len(run.carry.carried_ids),
            "image_carry_ack": bool(run.image_carry_ack),
            "host_checklist_job": bool(run.checklist_job),
        },
    )
    ticket_images = list(run.prospective_images)
    _notice = await _prepare_reply_notice(run, prep_id, missing_note)
    # The screens have ACTUALLY shipped now, so the tray entries can go --
    # and not one line earlier. Popping them where they were read killed
    # every capture on a Jira source, because that round returns the fetch
    # DIRECTIVE and prepares nothing.
    capture._drop_captures(run.capture_ids)
    return PreparePayloadResult(
        payload=payload,
        prep_id=prep_id,
        images=ticket_images,
        notice=_notice,
    )


# 2026-08-31 (C11): ids WERE sent and none resolved. That is a
# different situation from "never captured anything", and the generic
# menu never said so -- the tester re-reads five options wondering
# why option 3, which they already did, was ignored.
_BEAT1_MISSING_HEAD = "\n\n> ⚠️  The `capture_ids` on this call resolved to NOTHING: "
_BEAT1_MISSING_TAIL = (
    ". An id with a reason beside it says why; one without "
    "has expired or was never issued. Run `qa_capture_screens` "
    "again and send the NEW ids with `source_plan='jira_device'`."
)
