"""Moved from handlers (refactor P2): submit_suite."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, NamedTuple

from agents.host_ac import build_host_ac_section, extract_host_acs
from agents.host_category_jobs import normalize_category
from agents.host_checklist import build_host_checklist_section, extract_host_checklist
from agents.host_dedup import (
    apply_duplicate_groups,
    build_dup_contradiction_headline,
    build_dup_contradiction_pairs,
    build_dup_shortlist_counted,
    build_duplicate_section,
    dup_shortlist_cases_json,
    screen_duplicate_groups,
)
from agents.host_dedup import dup_agreements as host_dedup_dup_agreements
from agents.host_grounding import build_grounding_section, route_ungrounded_cases
from agents.host_image import (
    build_host_image_section,
    extract_host_image_descriptions,
    image_relevance_counts,
)
from agents.host_jobs import build_ambiguity_result_section, extract_ambiguity_result
from agents.host_prep_serde import PrepSerdeError, deserialize_prepared
from agents.host_submission import parse_host_suite, safe_prep_id
from agents.test_scenario_agent import CategoryResult, _finalize_generation
from config.settings import settings
from handlers import (
    _common,
    _elicit,
    _reply,
    export,
    generate,
    generate_prepare,
    prep_status,
    submit_category,
)
from tools import (
    case_quality_gate,
    dispatch_guard,
    prep_store,
    step_assertion,
    telemetry,
)
from tools.build_fingerprint import code_fingerprint
from tools.suite_store import load_suite, save_checklist, save_suite

logger = logging.getLogger("tools.mcp_handlers")


# part A
class _SubPrep(NamedTuple):
    prep_id: str
    envelope: dict
    prepared: Any
    meta: dict
    source_text: str
    source_url: Any
    version_note: str


class _SubRoute(NamedTuple):
    sidecar_obj: Any
    sidecar_raw: Any
    superseded_note: str


class _SubRead(NamedTuple):
    parsed: Any
    has_full: bool
    conflict_note: str
    rows: Any


# ops-6 (bug 1): warn (never block) when the prep was written by a
# different build than this one. Blocking would throw away the tester's
# generation work for what is usually harmless; silence is what let a
# split-version flow go unnoticed.
# 2026-08-03: this used to assert ONE cause -- "the server updated
# mid-flow" -- for a condition that has two, and the other one is
# worse. A real run staged its prep on a SEPARATE install (a dev
# checkout reporting v0.1.0) and submitted it to the packaged
# v1.34.0 server, because both were registered in one client and
# the agent split the flow across them. Two installs mean two
# `.env` files and two sets of feature flags, so the suite was
# prepared under one configuration and finalized under another --
# which "the suite below is fine unless something looks wrong"
# wrongly waves through. State the observed fact, name both causes,
# and let the version SHAPE hint at which: a self-update moves
# between released versions, while a dev checkout reports 0.x.
# F4: and the case the version string cannot express -- same
# version, different code. Appended rather than substituted: when
# BOTH differ, the tester needs the second install warning AND the
# fact that the prepared context itself is stale.
def _prep_version_note(meta: dict) -> str:
    version_note = ""
    try:
        _wrote = str(meta.get("app_version") or "")
        if _wrote and _common._BOOT_VERSION and _wrote != _common._BOOT_VERSION:
            version_note = prep_status._version_skew_note(_wrote, _common._BOOT_VERSION)
        version_note += prep_status._code_skew_note(
            str(meta.get("code_fingerprint") or ""),
            code_fingerprint(),
            version_matched=bool(_wrote and _wrote == _common._BOOT_VERSION),
        )
    except Exception:
        logger.debug("prep version check failed", exc_info=True)
    return version_note


# Fix 5b: without the finalized refusal, a resubmit would reach the
# sidecar/merge branches and finalize the same prep twice.
async def _load_submit_prep(prep_id: str) -> str | _SubPrep:
    envelope, refusal = await prep_status._load_open_prep(prep_id, require_dict=True)
    if refusal:
        return refusal

    try:
        prepared = deserialize_prepared(envelope.get("prepared") or {})
    except PrepSerdeError:
        logger.warning("host-mode submit: prep rehydrate failed", exc_info=True)
        return (
            f"⚠️ The staged preparation for prep_id `{prep_id}` could "
            "not be read (it may be corrupted or from an incompatible version). "
            "Start again with `qa_prepare_test_cases`."
        )

    meta = envelope.get("meta") or {}
    source_text = str(meta.get("source_text") or "")
    source_url = str(meta.get("source_url") or "") or None
    version_note = _prep_version_note(meta)
    return _SubPrep(
        prep_id, envelope, prepared, meta, source_text, source_url, version_note
    )


# CONFLICT RULE (item 4): a non-empty suite_json is AUTHORITATIVE and any
# accumulated per-category rows are ignored (the reply says how many were
# not used). An empty suite_json merges the accumulated rows instead.
# 2026-08-03 (Fix 1 follow-up): the MCP tool signature now accepts an
# OBJECT, so a host on the RECOMMENDED Path A route can send {} where
# it previously had to send "". The old `else: has_full = True` treated
# that as a full submission, which skipped the staged-row merge and
# then failed validation outright (TestSuite.test_cases carries
# min_length=1) -- turning the recommended finalize into a hard error
# and stranding every staged category. An empty object, or one whose
# only content is an empty `test_cases`, means exactly what "" means:
# merge the rows staged for this prep.
#
# A review SIDECAR is NOT empty -- it carries duplicate_groups /
# acceptance_criteria / ambiguity_result -- so it still takes
# has_full=True here and reaches _review_sidecar below, which is what
# keeps the Fix 2 route working.
def _has_full_submission(suite_json: Any) -> bool:
    if isinstance(suite_json, str):
        has_full = bool(suite_json.strip())
    elif suite_json is None:
        has_full = False
    elif isinstance(suite_json, dict):
        if not suite_json:
            has_full = False
        elif set(suite_json) <= {"test_cases"} and not suite_json.get("test_cases"):
            has_full = False
        else:
            has_full = True
    else:
        has_full = True
    return has_full


# Phase 3a: pass the prep meta so the two fold fields are
# recognised from THIS prep's stamps rather than from live
# flags that may have been flipped since prepare.
# F3 (2026-08-29): a sidecar-only submit that arrived BEFORE any
# category was staged is retained on the prep, not discarded.
#
# GUARDED, and the guard is the whole safety of this fix. A FULL
# submission (test_cases present) makes `_review_sidecar` return
# None, so an UNCONDITIONAL overlay would hand a non-None
# sidecar_obj to the branch below, which rebuilds the suite from
# STAGED ROWS ONLY -- silently discarding every case the host just
# sent, and returning "Nothing to finalize" when no rows exist.
# That path is reachable precisely because the ambiguity and
# step-assertion refusals both tell the host to resubmit under the
# same prep_id. So the overlay runs ONLY where the call was already
# going to take the sidecar or the bare-finalize route; Path B folds
# the retained fields into its own submission instead, below.
# The bound measures exactly the bytes supplied on THIS call, which
# is what its own docstring claims and what round 2 got wrong: the
# check used to sit below the retained-sidecar overlay, so a
# 19,425-byte submission was refused as "303062 bytes" -- 94% of it
# F3-retained content the tester can neither see nor shrink
# (measured, 2026-09-02 review round 2). Attributing the server's
# own bytes to the tester is the misreport class this workstream
# exists to remove, so the measurement happens before the overlay.
def _sidecar_route(suite_json: Any, meta: dict, has_full: bool) -> str | _SubRoute:
    sidecar_obj = prep_status._review_sidecar(suite_json, meta) if has_full else None
    _superseded_note = prep_status._superseded_sidecar_note(sidecar_obj, meta)
    _oversized = submit_category._oversized_submission_note(sidecar_obj)
    if _oversized:
        return _oversized
    if sidecar_obj is not None or not has_full:
        sidecar_obj = prep_status._merge_retained_sidecar(sidecar_obj, meta)
    sidecar_raw = (
        sidecar_obj.get("duplicate_groups") if isinstance(sidecar_obj, dict) else None
    )
    return _SubRoute(sidecar_obj, sidecar_raw, _superseded_note)


# F3 (2026-08-29): RETAIN it. Until now this branch read the
# sidecar, found nothing to merge it into and threw it away,
# so a host that ran step 0 before staging its categories
# lost its acceptance criteria, its checklist and its
# ambiguity verdict outright -- which is why the TICKET-5692
# run finalized with zero ACs, zero checklist items and
# host_ambiguity_ran:false. Only keys the sidecar actually
# MENTIONED are stored, so the present-but-empty discipline
# survives the round trip. Bounds are inherited rather than
# reinvented: update_prep rejects a payload over
# QA_PREP_MAX_BYTES, refuses an unknown/expired prep_id, and
# leaves created_at alone so QA_PREP_TTL_S /
# QA_PREP_MAX_LIFETIME_S still count from prepare.
# (inside the "meta" dict) Accumulates, and is deliberately NEVER
# cleared -- see _retained_sidecar. It
# lives as long as the prep does.
async def _try_retain_sidecar(
    prep_id: str, envelope: dict, meta: dict, sidecar_obj: Any
) -> bool:
    _mentioned = {
        k: sidecar_obj[k]
        for k in prep_status._SIDECAR_RETAINABLE
        if isinstance(sidecar_obj, dict) and k in sidecar_obj
    }
    _kept = False
    if _mentioned:
        try:
            _store = await prep_store.update_prep(
                prep_id,
                {
                    **envelope,
                    "meta": {
                        **meta,
                        "pending_sidecar": {
                            **prep_status._retained_sidecar(meta),
                            **_mentioned,
                        },
                    },
                },
            )
            _kept = not (_store or {}).get("error")
            if not _kept:
                logger.warning(
                    "prep %s sidecar NOT retained (%s)",
                    prep_id,
                    (_store or {}).get("error"),
                )
        except Exception:  # pragma: no cover - defensive
            logger.debug("sidecar retain failed", exc_info=True)
    return _kept


# Say so plainly. A success-shaped reply for a review that
# was NOT kept is the failure this fix exists to remove.
async def _sidecar_no_rows_reply(
    prep_id: str, envelope: dict, meta: dict, sidecar_obj: Any
) -> str:
    _keys_label = "`" + "` / `".join(prep_status._sidecar_keys(meta)) + "`"
    _kept = await _try_retain_sidecar(prep_id, envelope, meta, sidecar_obj)
    if _kept:
        return (
            "ℹ️ Received a review sidecar "
            f"({_keys_label}) but no per-category rows are "
            f"staged for prep_id `{prep_id}` yet. **It was "
            "RETAINED, not discarded** -- record your categories "
            "with `qa_submit_category`, then finalize with "
            f"`qa_submit_suite(prep_id='{prep_id}')` and this "
            "review will be applied. You do not need to send it "
            "again."
        )
    return (
        "⚠️ Nothing to finalize: received a review sidecar "
        f"({_keys_label}) but no per-category rows are staged "
        f"for prep_id `{prep_id}`, and it could NOT be retained "
        "on the prep. Record your categories with "
        "`qa_submit_category` and send this sidecar again with "
        "the finalize call."
    )


# Set each key only when the sidecar actually carried it:
# `duplicate_groups` present-but-empty reads downstream as "the
# host reviewed and found none", which an AC-only sidecar
# must not claim.
# duplicate_groups keeps the shipped _remap_dup_groups path,
# including its documented first-category-wins collision --
# the qualified-id contract that would have retired it was
# deleted on 2026-08-12 (default OFF, never validated).
# (The size bound now runs further up, before
# _merge_retained_sidecar, so it measures what the TESTER sent.)
# CRITICAL fix (review round 2): without this, Path A could
# never carry ambiguity_result at all -- see the _SIDECAR_KEYS
# comment above. Same present-but-empty discipline as the other
# two fields: a sidecar that did not mention it must not be read
# as "the host verified and found nothing".
# Residue R4: the checklist job's return field on Path A. THIS
# COPY IS THE POINT of adding `checklist_items` to
# _sidecar_keys -- recognition alone only makes the object COUNT
# as a sidecar. Without this line the merged dict (which
# _merge_category_rows builds from `test_cases` ONLY) reached
# parse_host_suite with no checklist at all, raw_checklist_items
# stayed None, extract_host_checklist returned ran=False, and
# the reply told the tester the submission "carried no usable
# checklist_items field" -- while the host HAD sent one, and
# the staged route finished with no requirements checklist at
# all, silently. Keyed off the prep's META STAMP, with
# the same present-but-empty discipline as every field above.
# Deliberately NO id remap: CL-NNN ids are assigned server-side
# in extract_host_checklist and are never tc_ids, so 3a's
# _remap_risk_scores problem -- every staged category restarting
# at TC-001 -- structurally cannot arise here.
# 2026-08-09: the image job's return field on Path A.
# `image_descriptions` was already RECOGNISED as a sidecar key
# (_sidecar_keys) but was never COPIED here, unlike every field
# above -- and _merge_category_rows builds merged_dict from
# `test_cases` ONLY. So on the per-category route the host's
# descriptions (and now its relevance verdicts) were silently
# dropped, raw_image_descriptions stayed None, and the reply told
# the tester the submission "carried no readable
# image_descriptions" while the host HAD sent them -- the same
# class of silent loss residue R4 fixed for checklist_items.
# Keyed off the prep's META STAMP with the same
# present-but-empty discipline as every field above. NO id remap
# is possible or needed: entries are keyed by image position,
# never by tc_id, so the _remap_risk_scores collision problem
# structurally cannot arise here.
# F7 (2026-09-02 audit): NO size cap on the SERVER's OWN
# merge. QA_PREP_MAX_BYTES bounds what a HOST submits, and
# every staged row was already measured against it at
# qa_submit_category time; applying it again to their
# concatenation meant eight ACCEPTED categories finalized
# to 'submitted JSON exceeds the N-byte cap ... fix the
# JSON and resubmit' -- JSON the tester never sent and
# cannot fix, with no route left to their own cases.
def _sidecar_merge(rows: list, route: _SubRoute, meta: dict) -> tuple[Any, int]:
    sidecar_obj = route.sidecar_obj
    sidecar_raw = route.sidecar_raw
    merged_dict, used, id_map = prep_status._merge_category_rows(rows)
    merged_dict = dict(merged_dict)
    if sidecar_raw is not None:
        merged_dict["duplicate_groups"] = prep_status._remap_dup_groups(
            sidecar_raw, id_map
        )
    _sidecar_acs = sidecar_obj.get("acceptance_criteria")
    if _sidecar_acs is not None:
        merged_dict["acceptance_criteria"] = _sidecar_acs
    _sidecar_amb = sidecar_obj.get("ambiguity_result")
    if _sidecar_amb is not None:
        merged_dict["ambiguity_result"] = _sidecar_amb
    if meta.get("host_checklist_job"):
        _sidecar_checklist = sidecar_obj.get("checklist_items")
        if _sidecar_checklist is not None:
            merged_dict["checklist_items"] = _sidecar_checklist
    if meta.get("host_image_job"):
        _sidecar_images = sidecar_obj.get("image_descriptions")
        if _sidecar_images is not None:
            merged_dict["image_descriptions"] = _sidecar_images
    parsed = parse_host_suite(merged_dict, enforce_size_cap=False)
    return parsed, used


# CRITICAL (iteration 5): the SIDECAR finalize merges the same
# staged rows and deletes the prep on success, so it needs the
# same completeness gate as the bare empty finalize below --
# otherwise the route these instructions now recommend for
# keeping the duplicate review on Path A would silently ship a
# truncated suite after a host crash.
async def _read_via_sidecar(
    prep_id: str, envelope: dict, meta: dict, route: _SubRoute
) -> str | _SubRead:
    rows_res = await prep_store.load_submissions(prep_id)
    _store_note = prep_status._prep_store_error_reply(prep_id, rows_res)
    if _store_note:
        return _store_note
    rows = rows_res.get("content") or []
    if not rows:
        return await _sidecar_no_rows_reply(prep_id, envelope, meta, route.sidecar_obj)
    _gate = prep_status._fanout_incomplete_note(meta, rows, prep_id)
    if _gate:
        return _gate
    parsed, used = _sidecar_merge(rows, route, meta)
    has_full = False
    _keys_label = "`" + "` / `".join(prep_status._sidecar_keys(meta)) + "`"
    conflict_note = route.superseded_note + (
        f"> ℹ️  Finalized from {used} accumulated per-category "
        f"row(s) plus a review sidecar ({_keys_label}) (no full "
        "suite_json test_cases were submitted).\n\n"
    )
    return _SubRead(parsed, has_full, conflict_note, rows)


# The TESTER's own submission is measured, in the representation
# the cap is denominated in (`_submission_bytes` mirrors
# parse_host_suite's own two branches), with a message that only
# claims what this branch knows. Then the SERVER's retained
# sidecar is folded in and the result parsed EXEMPT, because
# charging the server's retained bytes to the tester is the
# defect this closes: measured at the default 4 MB cap, an
# 87,498-byte submission was refused as "exceeds the
# 4000000-byte cap -- Fix the JSON", and the effective cap
# collapsed to ~67 KB for the life of that prep.
#
# This is the second attempt. The first reused
# `_oversized_submission_note`, which measures `json.dumps(obj)`
# unconditionally and so RE-ESCAPED a string submission (57,129
# bytes reported as 64,297), and whose wording talks about
# "review fields" -- false for an object that is the whole
# suite. Those two mistakes are the two invariants named above
# each helper, and they are why this branch has its own.
# L2 from the 2026-09-02 review, recorded because the kwarg's
# NAME under-describes it: `enforce_size_cap=False` also skips
# `parse_host_suite`'s "submitted object is not
# JSON-serialisable" refusal, which lives inside the same
# `if cap and enforce_size_cap` block. Measured, not read: the
# same dict with one opaque value raises under True and parses
# under False. Bounded and accepted -- MCP tool arguments have
# already been JSON-decoded, so only an in-process caller can
# deliver such an object, and the bound itself is not lost
# (`_oversized_full_suite_note` above measures every
# JSON-expressible shape byte-exactly). Noted so a future
# reader does not infer the flag touches only the cap.
async def _read_full_suite(prep_id: str, suite_json: Any, meta: dict) -> str | _SubRead:
    conflict_note = ""
    _oversized_full = _reply._oversized_full_suite_note(suite_json)
    if _oversized_full:
        return _oversized_full
    parsed = parse_host_suite(
        prep_status._with_retained_sidecar(suite_json, meta),
        enforce_size_cap=False,
    )
    rows_res = await prep_store.load_submissions(prep_id)
    rows = rows_res.get("content") or []
    n_rows = len(rows)
    if n_rows:
        conflict_note = (
            f"> ℹ️  A full suite was submitted, so {n_rows} "
            "accumulated per-category row(s) were NOT used.\n\n"
        )
    return _SubRead(parsed, True, conflict_note, rows)


# A store failure here would otherwise read as 'no rows are
# staged', which is the one thing it cannot establish.
# Path A completeness gate: when THIS prep requested parallel
# fan-out, refuse to finalize a partial staged set. Path B
# (non-empty suite_json) is unaffected (has_full branch above).
# Shared with the sidecar branch above -- ONE decision point.
# F7: server-built merge -- see the sidecar branch above.
async def _read_staged_rows(prep_id: str, meta: dict) -> str | _SubRead:
    rows_res = await prep_store.load_submissions(prep_id)
    _store_note = prep_status._prep_store_error_reply(prep_id, rows_res)
    if _store_note:
        return _store_note
    rows = rows_res.get("content") or []
    if not rows:
        return (
            "⚠️ Nothing to finalize: no suite_json was provided "
            "and no per-category rows are staged for prep_id "
            f"`{prep_id}`. Submit the merged JSON, or record categories "
            "first with `qa_submit_category`."
        )
    _gate = prep_status._fanout_incomplete_note(meta, rows, prep_id)
    if _gate:
        return _gate
    merged_dict, used, _id_map = prep_status._merge_category_rows(rows)
    parsed = parse_host_suite(merged_dict, enforce_size_cap=False)
    conflict_note = (
        f"> ℹ️  Finalized from {used} accumulated per-category "
        "row(s) (no full suite_json was submitted).\n\n"
    )
    return _SubRead(parsed, False, conflict_note, rows)


# 2026-08-31: "Fix the JSON" is right for a parse failure and WRONG
# for a validation failure -- there the JSON parsed perfectly and
# every case was rejected on a FIELD rule, so a host reading this
# went looking for a syntax error that was not there. The reason
# text now names the fields; steer the reader at them.
async def _read_submission(
    prep_id: str, envelope: dict, meta: dict, suite_json: Any, has_full: bool
) -> str | _SubRead:
    try:
        route = _sidecar_route(suite_json, meta, has_full)
        if isinstance(route, str):
            return route
        if route.sidecar_obj is not None:
            return await _read_via_sidecar(prep_id, envelope, meta, route)
        elif has_full:
            return await _read_full_suite(prep_id, suite_json, meta)
        else:
            return await _read_staged_rows(prep_id, meta)
    except PrepSerdeError as exc:
        _fix = (
            "Fix the fields named above on each case and resubmit"
            if "failed validation" in str(exc)
            else "Fix the JSON and resubmit"
        )
        return (
            f"⚠️ Could not read the submitted suite: {exc}\n\n"
            f"{_fix} with the same prep_id `{prep_id}`."
        )


# part B
class _SubCases(NamedTuple):
    all_cases: list
    staged_rows: Any
    submitted_cases: list
    cat_source: str


class _SubGates(NamedTuple):
    volume_note: str
    assertion_note: str
    assert_findings: Any
    data_findings: Any
    dropped_note: str


class _SubAmb(NamedTuple):
    result: Any
    note: str


class _SubAc(NamedTuple):
    result: Any
    note: str


class _SubGround(NamedTuple):
    note: str
    assumed_rows: list
    all_cases: list


class _SubChecklist(NamedTuple):
    note: str
    gap: bool


class _SubAcks(NamedTuple):
    volume_floor: bool
    image_relevance: bool
    step_assertion: bool
    quality_gate: bool


# F6: on the FULL-suite path the per-case `category` is host self-report --
# the server has no grouping of its own there. Normalise onto a canonical
# name, blank anything unresolvable, and TAG the provenance so a later
# re-export can still tell it from a server-derived value.
def _self_reported_category_note(all_cases: list) -> str:
    _resolved = 0
    for _tc in all_cases:
        _canon = normalize_category(getattr(_tc, "category", None))
        try:
            _tc.category = _canon or None
            _tc.category_source = "host" if _canon else None
        except Exception:  # pragma: no cover - defensive
            logger.debug("could not set category", exc_info=True)
        if _canon:
            _resolved += 1
    _unresolved = len(all_cases) - _resolved
    cat_source = (
        f"> ℹ️  Category resolved for {_resolved} case(s)"
        + (f", unresolved for {_unresolved}" if _unresolved else "")
        + " -- **self-reported by your chat model**, because a single "
        "merged submission carries no server-side grouping. Submit per "
        "category with `qa_submit_category` for a server-derived "
        "category instead.\n\n"
    )
    return cat_source


# How many per-category rows the server ALREADY holds. Read ONCE here
# and PASSED to every refusal and hand-back below (none of them re-reads
# the store), so they all describe the same reality. ZERO on the merged
# route: `has_full` means the staged rows were explicitly NOT used, so
# pointing a host at them would finalize a different suite from the one
# it just sent.
# Snapshot of what the HOST actually sent, taken before anything narrows
# `all_cases`. The duplicate and coverage reviews resolve the ids they were
# given against this list, so it has to keep meaning "the submission" even
# after the grounding review re-files a case out of the executable suite --
# otherwise a group or a coverage claim naming a re-filed case silently
# stops resolving and the tester is told something untrue about their own
# submission.
async def _case_intake(prep_id: str, read: _SubRead) -> _SubCases:
    all_cases = list(read.parsed.suite.test_cases)
    staged_rows = (
        0 if read.has_full else await submit_category._staged_row_count(prep_id)
    )
    submitted_cases = list(all_cases)
    cat_source = ""
    if read.has_full:
        cat_source = _self_reported_category_note(all_cases)
    return _SubCases(all_cases, staged_rows, submitted_cases, cat_source)


# Batch 1 (2026-08-09) volume gate and F4 (2026-08-29) step-assertion
# gate, in that order, on BOTH finalize routes. They run HERE: after
# the has_full loop above normalised every self-reported `category`,
# and before the ambiguity gate, the finalize, the export and the
# persist -- so a refusal costs one round trip and destroys nothing.
# version_note is prefixed to each refusal: a prep staged on one
# install and submitted to another must not lose it to an early return.
async def _submit_gates(
    prep: _SubPrep, cases: _SubCases, parsed: Any, acks: _SubAcks
) -> str | _SubGates:
    prep_id = prep.prep_id
    envelope = prep.envelope
    version_note = prep.version_note
    all_cases = cases.all_cases
    staged_rows = cases.staged_rows
    _refusal, volume_note = await submit_category._volume_floor_gate(
        prep_id, envelope, all_cases, staged_rows, acks.volume_floor
    )
    if _refusal is not None:
        return f"{version_note}{_refusal}"
    _assert_findings = step_assertion.find_tautological_steps(all_cases)
    _refusal, assertion_note = await submit_category._step_assertion_gate(
        prep_id, envelope, all_cases, _assert_findings, acks.step_assertion
    )
    if _refusal is not None:
        return f"{version_note}{_refusal}"
    _data_findings = step_assertion.find_echoed_test_data(all_cases)
    dropped_note = prep_status._dropped_note(parsed)
    return _SubGates(
        volume_note, assertion_note, _assert_findings, _data_findings, dropped_note
    )


# The prep is deliberately NOT deleted: the tester can run
# the preflight and resubmit the same suite with the same
# prep_id. Refusing must cost a round trip, not the work.
# staged_count is passed, not re-read: on the MERGED route the
# staged rows were superseded by this very submission, and a
# re-read here would tell the host its cases are safely held
# when finalizing from them would build a different suite.
# F4 (2026-08-30): the refusal used to give ONE instruction --
# "run step 0, then resubmit" -- for two different states, and
# for one of them that instruction cannot be satisfied.
# `HostAmbiguityResult.cleared` is `ran and severity in
# (none, low, medium)`, so a `high` verdict never clears; there
# is no ack argument and no override. A host that had ALREADY
# run step 0, reached `high` and reported it honestly was told
# to run step 0 and resubmit, which loops forever. The policy is
# right -- an untestable ticket should not yield a suite -- so
# what changes is only what the tester is told to DO about it.
async def _ambiguity_refusal(
    prep_id: str, amb_result: Any, amb_note: str, staged_rows: int
) -> str:
    await _common._audit(
        "mcp_submit_suite_refused",
        entity_id=prep_id,
        detail={
            "reason": "ambiguity_result",
            "severity": amb_result.severity or "absent",
        },
    )
    _how = await submit_category._staged_resubmit_hint(
        prep_id, "ambiguity_result", staged_count=staged_rows
    )
    if amb_result.ran and amb_result.severity == "high":
        return (
            f"{amb_note}⛔ **Submission refused:** your own "
            "ambiguity preflight rated this source **high** — too "
            "under-specified to write trustworthy test cases from. "
            "Re-running step 0 will not change that, and there is no "
            "acknowledgement argument that overrides it.\n\n"
            "**Do this instead:** put the unanswered questions above "
            "to the tester, get the ticket or the description "
            "amended, then call `qa_prepare_test_cases` again with "
            "the fuller source. An operator who accepts the risk can "
            "set `QA_HOST_AMBIGUITY_REQUIRE_RESULT=false`, which "
            "discloses the verdict and returns the suite instead of "
            "refusing it. Nothing was discarded — prep_id "
            f"`{prep_id}` still holds this work."
        )
    return (
        f"{amb_note}⛔ **Submission refused:** `QA_HOST_AMBIGUITY_REQUIRE_RESULT` is on and this submission "
        "carries no cleared ambiguity preflight. Run step 0 of "
        f"the payload's `jobs_to_run`, then {_how}. "
        "Nothing was discarded."
    )


# The ambiguity job's verdict. QA_HOST_AMBIGUITY_REVIEW_ENABLED
# removed this server's classifier call AND, until now, every
# signal that the blocking preflight ran at all -- submit accepted a
# suite identically whether the host obeyed step 0 or skipped it.
# Keyed off the prep's meta stamp (not the live flag) so a mid-flow
# .env flip cannot change an in-flight prep. UNTRUSTED and NOT a
# permission bit: a host that lies "none" is not stopped here. What
# it buys is that "no verdict" stops looking like "cleared".
# getattr, not direct access (review round 2 MINOR): every
# other new flag in this program reads this way, so a
# partial rollback of just the settings-field edit does not
# turn every host submit into a bare AttributeError.
async def _ambiguity_review(
    prep_id: str, meta: dict, parsed: Any, staged_rows: int
) -> str | _SubAmb:
    amb_result = None
    amb_note = ""
    if meta.get("host_ambiguity_review"):
        amb_result = extract_ambiguity_result(
            getattr(parsed, "raw_ambiguity_result", None)
        )
        amb_note = build_ambiguity_result_section(amb_result)
        if (
            getattr(settings, "qa_host_ambiguity_require_result", False)
            and not amb_result.cleared
        ):
            return await _ambiguity_refusal(prep_id, amb_result, amb_note, staged_rows)
    return _SubAmb(amb_result, amb_note)


# The AC boomerang's return field. It rode in on THIS submission -- no
# extra round trip and no server-side LLM call -- and is UNTRUSTED, so
# host_mode.extract_host_acs shape-validates it, re-canonicalises the
# ids and strips URLs before anything reads it. Adopted into
# prepared.acs (which _finalize_generation reads for the RTM) but
# NEVER into prepared.source_acs: source_acs is the ground truth the
# AC-anchoring check anchors against, and a model-derived criterion is
# not a ticket requirement. Runs only when THIS prep shipped the job,
# so a normal submit is byte-identical.
def _ac_review(meta: dict, parsed: Any, prepared: Any, all_cases: list) -> _SubAc:
    ac_result = None
    ac_note = ""
    if meta.get("host_ac_job"):
        ac_result = extract_host_acs(getattr(parsed, "raw_acceptance_criteria", None))
        if ac_result.ran and not getattr(prepared, "acs", None):
            prepared.acs = list(ac_result.acs)
        ac_note = build_host_ac_section(ac_result, all_cases)
    return _SubAc(ac_result, ac_note)


# The entailment review's verdicts rode in on THIS submission -- no extra
# round trip and no server-side LLM call -- and are UNTRUSTED, so
# tools.grounding_verdicts matches every id against the submitted suite,
# enum-gates the verdicts, caps the notes and refuses a batch that marks
# more than 40% of the suite ungrounded. It never removes a case: an
# ungrounded one is REPORTED for a human to confirm or delete, because it
# may be a real requirement nobody wrote down. "" when the optional field
# is absent, so a normal submit is byte-identical.
# Re-file the cases that review judged ungrounded. This must happen BEFORE
# _finalize_generation for exactly the reason the duplicate review does:
# finalize RENUMBERS every tc_id, so anything keyed to the ids the host
# submitted has to act first. The removed cases are NOT deleted -- they go
# to their own workbook sheet with their own AR-nnn ids (a retained TC-nnn
# would collide with an unrelated case that inherited that number), so a
# requirement nobody wrote down is still in front of a human.
def _grounding_review(parsed: Any, all_cases: list) -> _SubGround:
    grounding_note = build_grounding_section(
        getattr(parsed, "raw_grounding_verdicts", None), all_cases
    )
    assumed_rows: list = []
    if grounding_note:
        routing = route_ungrounded_cases(
            getattr(parsed, "raw_grounding_verdicts", None), all_cases
        )
        if routing is not None and routing.routed:
            assumed_rows = routing.rows
            removed = {c.tc_id for c in routing.routed}
            all_cases = [c for c in all_cases if c.tc_id not in removed]
            logger.info(
                "Grounding review moved %d case(s) to the Assumed Requirements sheet",
                len(routing.routed),
            )
    return _SubGround(grounding_note, assumed_rows, all_cases)


# Residue R4: the checklist boomerang's return field. It rode in on THIS
# submission -- no extra round trip and no server-side LLM call -- and
# is UNTRUSTED, so host_mode.extract_host_checklist shape-validates it,
# strips URLs, caps it and ASSIGNS every CL-NNN id before anything reads
# it. Adopted onto `prepared` so the XLSX sheets read it unchanged.
#
# presented_ids is EVERY returned id: the host had the whole list in its
# own context, so the QA_CHECKLIST_MAX_PROMPT_CHARS "NOT PRESENTED TO
# GENERATOR" bucket does not apply here and must not be faked.
# audit_granularity is pure Python and runs SERVER-side -- it is the
# independent counterweight that makes host authorship of the
# requirement set defensible.
# F6 (2026-08-15): "there is no note" and "the note says no checklist
# arrived" are different states, and only the second is a capability
# loss worth hoisting and recording. Tracked explicitly rather than
# sniffed out of the rendered markdown.
# CARRIED FORWARD (residue R4, review iteration 3): what this prep
# already holds from an earlier submit of the SAME prep_id. The host
# is not told to resend `checklist_items` on a resubmit and
# reasonably does not, so without this a resubmit would finalize
# with no requirement checklist at all.
# Keep the carried list EXACTLY as it is (a resubmission must not
# silently re-scope the requirement set) and reuse its stored
# audit, so the granularity score is the one that was computed
# over this very list.
# A carried-forward list is NOT a gap: the requirements are still in
# force and the traceability report still reads them.
def _checklist_review(meta: dict, parsed: Any, prepared: Any) -> _SubChecklist:
    checklist_note = ""
    checklist_gap = False
    if meta.get("host_checklist_job"):
        from tools.atomic_checklist import audit_granularity

        _cl_result = extract_host_checklist(
            getattr(parsed, "raw_checklist_items", None)
        )
        _cl_audit: dict = {}
        _cl_carried = len(list(getattr(prepared, "checklist_items", None) or []))
        if _cl_result.ran:
            _cl_audit = audit_granularity(_cl_result.items)
            prepared.checklist_items = list(_cl_result.items)
            prepared.checklist_presented_ids = [it.item_id for it in _cl_result.items]
            prepared.checklist_audit = _cl_audit
        elif _cl_carried:
            _cl_audit = dict(getattr(prepared, "checklist_audit", None) or {})
        checklist_note = build_host_checklist_section(
            _cl_result, _cl_audit, carried=(0 if _cl_result.ran else _cl_carried)
        )
        checklist_gap = not _cl_result.ran and not _cl_carried
    return _SubChecklist(checklist_note, checklist_gap)


class _SubImages(NamedTuple):
    note: str
    result: Any
    counts: dict


# TWO-BEAT ack, copied from the volume gate: mark the prep as
# refused so a LATER image_relevance_ack is honoured, while an
# ack sent on the FIRST submit -- which the tester cannot have
# seen this finding for -- is refused and told so. Non-fatal: an
# unpersisted mark only means the next ack is refused again,
# which fails in the SAFE direction.
async def _mark_image_refused(prep: _SubPrep) -> None:
    prep_id = prep.prep_id
    envelope = prep.envelope
    meta = prep.meta
    try:
        _mark = await prep_store.update_prep(
            prep_id,
            {
                **envelope,
                "meta": {**meta, "image_relevance_refused": True},
            },
        )
        if (_mark or {}).get("error"):
            logger.warning(
                "prep %s not marked image_relevance_refused (%s)",
                prep_id,
                (_mark or {}).get("error"),
            )
    except Exception:  # pragma: no cover - never block the refusal
        logger.debug("image_relevance_refused mark failed", exc_info=True)


# Batch 4 LAYER 2: the opt-in refusal. It runs HERE -- after the
# sidecar/merge branches above, so it sees BOTH finalize routes, and
# before _finalize_generation, the export and the persist, so a
# refusal costs one round trip and destroys nothing (the prep is
# kept, no staged row is dropped),
# exactly like the ambiguity and volume refusals. img_note is
# prefixed so the tester sees the off-topic finding itself, not just
# the refusal, and version_note for the same reason the two gates
# above prefix it.
# Same defect class as the volume refusal: on the staged route
# the cases are already on the server, so option 2 must ask for
# the missing verdicts ALONE. Computed only when rows exist, so
# a merged submit's reply is byte-identical to today's.
async def _image_gate_verdict(
    meta: dict, img_result: Any, prep_id: str, staged_rows: int, ack: bool
) -> tuple[str, str]:
    _imode, _imd = prep_status._image_relevance_gate(
        meta,
        img_result,
        prep_id,
        ack=bool(ack),
        staged_hint=(
            await submit_category._staged_resubmit_hint(
                prep_id,
                "image_descriptions",
                field_shape=(
                    "[one entry per image, each with `relevant` (the "
                    "bare string `yes`, `no` or `unsure`) and a "
                    "one-line `relevance_reason`]"
                ),
                staged_count=staged_rows,
            )
            if staged_rows
            else ""
        ),
    )
    return _imode, _imd


async def _image_gate_effects(prep: _SubPrep, imode: str, counts: dict) -> None:
    prep_id = prep.prep_id
    if imode == "refuse":
        await _mark_image_refused(prep)
        await _common._audit(
            "mcp_submit_suite_refused",
            entity_id=prep_id,
            detail={
                "reason": "image_relevance",
                "host_image_off_topic": int(counts.get("no") or 0),
                "host_image_relevance_ran": bool(counts.get("ran")),
            },
        )
    if imode == "acked":
        await _common._audit(
            "mcp_submit_suite_image_override",
            entity_id=prep_id,
            detail={
                "reason": "image_relevance_ack",
                "host_image_off_topic": int(counts.get("no") or 0),
            },
        )


# The image job's return field. This server described NOTHING itself on
# this prep -- no ask_vision at all -- so the host's own multimodal model
# is the only thing that read the screenshots. UNTRUSTED (pixels can be
# attacker-controlled just like the _GUARD-wrapped ticket text): shape
# validated, URL-stripped, newline-collapsed and capped by host_mode
# before anything renders it, and it feeds NO prompt and NO exporter
# field. Keyed off the prep's meta stamp, so a mid-flow .env flip cannot
# change an in-flight prep and a normal submit is byte-identical.
# v1.97.0 cursor-hardening (item 2): disclosed here, unconditionally,
# because a skipped text-only plan never triggers a host image job at
# all (see host_image_job below) -- this must not be nested under it.
# Batch 4 LAYER 3: hoisted OUT of the block below so the submit audit
# row can read them. They stay None / {} on a prep that shipped no image
# job, which is what keeps that row byte-identical to today's.
# Keyed off the prep's own stamp, never the live flag: an OLD
# envelope has no stamp, so no verdict is parsed and nothing is
# warned about.
# The attested-count channel has no server-side evidence at all, so
# an empty return field is reported instead of assumed benign.
# BOTH intake channels now. The captured count is a 2026-08-09
# stamp, so it is absent (-> 0) on an old envelope and this reads
# exactly as before. Raw stamps are passed through: the helper
# coerces them inside its own try, so a garbage stamp cannot raise
# here either.
async def _image_review(
    prep: _SubPrep, parsed: Any, staged_rows: int, acks: _SubAcks
) -> str | _SubImages:
    meta = prep.meta
    version_note = prep.version_note
    img_note = ""
    img_note += generate_prepare._text_only_image_skip_note(meta)
    _img_result = None
    _img_counts: dict = {}
    if meta.get("host_image_job"):
        _img_result = extract_host_image_descriptions(
            getattr(parsed, "raw_image_descriptions", None),
            relevance=bool(meta.get("host_image_relevance")),
        )
        img_note += build_host_image_section(_img_result)
        img_note += generate_prepare._attested_image_gap_note(
            meta.get("attached_image_count"),
            _img_result,
            captured=meta.get("captured_image_count"),
        )
        _img_counts = image_relevance_counts(_img_result)
        _imode, _imd = await _image_gate_verdict(
            meta, _img_result, prep.prep_id, staged_rows, acks.image_relevance
        )
        await _image_gate_effects(prep, _imode, _img_counts)
        if _imode == "refuse":
            return f"{version_note}{img_note}{_imd}"
        if _imode == "acked":
            img_note += _imd
    return _SubImages(img_note, _img_result, _img_counts)


class _SubDup(NamedTuple):
    all_cases: list
    review_on: bool
    apply: bool
    groups: list
    notes: list
    groups_submitted: int
    removed: list
    note: str
    agreements: list
    status_note: str


# Separate variable ON PURPOSE: dup_note is reassigned wholesale below.
# Set on BOTH paths with DIFFERENT wording (F11): the full-suite path can
# fairly say the host did not send the field; the merge path must blame
# the route, because the SERVER is what discards it there.
# F11: the merge route cannot carry duplicate_groups at all --
# _merge_category_rows copies only test_cases. Saying nothing here read
# as "reviewed, found none"; blaming the host would be wrong, because
# the SERVER discards the field on this path. Name the route instead.
#
# 2026-08-03 (Fix 2 / H4): an HONEST EMPTY SIDECAR lands here too, and
# for it this message is a FALSEHOOD. A host that staged categories,
# reviewed the merged set and found no duplicates reports that by
# sending a sidecar whose `duplicate_groups` is []. The sidecar branch
# sets has_full=False (see the merge branch above), so gating only on
# has_full told that host "No duplicate review ran ... duplicates are
# still present" -- the opposite of what it just did. It copies the
# field into the merged dict EVEN WHEN EMPTY (`sidecar_raw is not
# None`), so parse_host_suite sets duplicate_review_offered and the two
# cases are distinguishable. This mattered little while the empty
# finalize was the recommended route; it is the COMMON case now that
# the sidecar is recommended whenever this review is on.
# D4 (2026-08-21), reviewer item 4. This used to read
# "Duplicate review ran and reported no cross-category
# duplicates." -- flat, unattributed, and the MAJORITY
# outcome, because the server prescreen added below
# recovers only about one redundant cluster in nine.
# A false assurance standing unchallenged is the whole
# of D4, so the common path could not keep it. This is
# a REPLACEMENT, not an addition: +152 chars inside a
# section that is already protected, so the reply gains
# attribution without gaining a slot, without a new
# omission-marker risk, and without touching the
# tests/test_finalize_reply_budget.py fixture, which
# reports NO review and therefore renders the `else`
# branch below instead of this one.
def _dup_status_note(
    parsed: Any, has_full: bool, dup_groups: list, dup_review_on: bool
) -> str:
    dup_status_note = ""
    if dup_review_on and not has_full and not dup_groups:
        if getattr(parsed, "duplicate_review_offered", False):
            dup_status_note = (
                "> ♻️  Duplicate review ran: the HOST "
                "reported no cross-category duplicates -- its verdict, "
                "not a server check. The server's title prescreen is a "
                "weak floor (1 of 9 clusters, measured), so neither "
                "empty result is evidence.\n\n"
            )
        else:
            dup_status_note = (
                "> ℹ️  No duplicate review ran: this suite was "
                "finalized from per-category rows with no `duplicate_groups` "
                "sidecar. Either submit ONE merged `suite_json`, or finalize with "
                '`suite_json={"duplicate_groups":[[...]]}` (empty/absent '
                "`test_cases`) after staging categories -- send an EMPTY "
                "`duplicate_groups` list (`[]`) there if you DID review and "
                "found none, which records the review as run. Any "
                "cross-category duplicates are still present.\n\n"
            )
    elif dup_review_on and has_full and not dup_groups:
        if getattr(parsed, "duplicate_review_offered", False):
            dup_status_note = (
                "> ♻️  Duplicate review ran: the HOST "
                "reported no cross-category duplicates -- its verdict, "
                "not a server check. The server's title prescreen is a "
                "weak floor (1 of 9 clusters, measured), so neither "
                "empty result is evidence.\n\n"
            )
        else:
            dup_status_note = (
                "> ℹ️  Duplicate review was requested but this "
                "submission carried no `duplicate_groups` field, so NO "
                "duplicate review ran -- which is NOT the same as finding "
                "none. To report a review that found none, send the field "
                'as an EMPTY list (`"duplicate_groups": []`). Any '
                "cross-category duplicates are still present.\n\n"
            )
    return dup_status_note


# Piece 1: the host's OPTIONAL cross-category duplicate review. It rode in
# on THIS submission -- no extra round trip and no server-side LLM call --
# and its SHAPE was validated against the submitted tc_ids inside
# host_mode._extract_duplicate_groups. Shape validation is not a safety
# bound (it permits a disjoint partition of the suite), so
# host_mode.screen_duplicate_groups runs next, in BOTH modes: the reply then
# shows exactly what the server would act on, and every refusal is
# disclosed. Removal additionally needs QA_HOST_DEDUP_APPLY and MUST happen
# here, BEFORE _finalize_generation, because that renumbers every tc_id.
# `submitted_cases` was captured where the submission was read, NOT here:
# by this point the grounding review may have narrowed `all_cases`, and the
# reviews below need the ids the host actually sent.
# Advisory, shown next to every group in BOTH modes. Never a veto -- see
# host_mode.dup_agreements for the measurements that forbid gating on it.
def _dup_review(parsed: Any, has_full: bool, all_cases: list) -> _SubDup:
    dup_review_on = True
    dup_apply = bool(settings.qa_host_dedup_apply)
    dup_groups = list(getattr(parsed, "duplicate_groups", None) or [])
    dup_notes = list(getattr(parsed, "duplicate_notes", None) or [])
    dup_groups_submitted = len(dup_groups)
    dup_removed: list = []
    dup_note = ""
    dup_agreements: list = []
    dup_status_note = _dup_status_note(parsed, has_full, dup_groups, dup_review_on)
    if dup_review_on and dup_groups:
        dup_agreements = host_dedup_dup_agreements(all_cases, dup_groups)
        if dup_apply:
            screened, screen_notes = screen_duplicate_groups(all_cases, dup_groups)
            dup_notes += screen_notes
            if screened:
                all_cases, dup_removed, apply_notes = apply_duplicate_groups(
                    all_cases, screened
                )
                dup_notes += apply_notes
    return _SubDup(
        all_cases,
        dup_review_on,
        dup_apply,
        dup_groups,
        dup_notes,
        dup_groups_submitted,
        dup_removed,
        dup_note,
        dup_agreements,
        dup_status_note,
    )


class _SubRun(NamedTuple):
    prep: _SubPrep
    read: _SubRead
    cases: _SubCases
    gates: _SubGates
    amb: _SubAmb
    ac: _SubAc
    ground: _SubGround
    checklist: _SubChecklist
    images: _SubImages
    dup: _SubDup


# part D
class _SubFinal(NamedTuple):
    suite: Any
    summary: Any
    status: Any
    fa_skip_note: str


class _SubQuality(NamedTuple):
    note: str


class _SubPost(NamedTuple):
    fin: _SubFinal
    volume_note: str
    quality_note: str
    dup_note: str


class _SubProv(NamedTuple):
    dup_case_note: str
    dup_counts: Any
    provenance: Any
    session_note: str


class _SubSaved(NamedTuple):
    suite_id: str
    persist_note: str
    prov: _SubProv


# Honesty rule: a suppression the tester cannot see IS a silent
# downgrade. Emitted only in the exact situation that changed -- the
# standalone tool is enabled, so the tester has reason to expect a
# report, but the inline one is off on this path. Attached to the
# FINALIZE reply only: the gap reply is an intermediate "regenerate and
# resubmit" instruction, not the delivered artifact, and the note would
# otherwise repeat on every round.
# 2026-08-03: the test-cases-only edition no longer registers
# qa_feature_analysis, so "call it on demand" would name a tool the
# tester's client cannot see. The edition gate is the only thing
# deciding that now (batch 8c hardcoded the feature ON).
def _fa_skip_note() -> str:
    fa_skip_note = ""
    if _common._feature_analysis_enabled() and not _common._test_cases_only():
        fa_skip_note = (
            "> ℹ️  Feature Analysis report SKIPPED for this "
            "host-mode submit. Call `qa_feature_analysis` on demand if "
            "you want it -- it is chat-only, like everything else here.\n\n"
        )
    return fa_skip_note


# ONE synthetic CategoryResult so _finalize_generation's "N of 8 failed"
# partial line correctly does NOT fire (its only use of category_results).
# The exporter reads this PrivateAttr to add the sheet; absent means
# no sheet, so a run with the review off is byte-identical.
# The FOURTH suppression that used to sit here -- feature_report_enabled=False,
# against the inline Feature Analysis report, measured at 42.0s on the
# 2026-07-30 host-mode run -- became unnecessary on 2026-08-16, when
# dead-code deletion P2-E3 removed the branch and analyze_feature with
# it. There is no argument left to pass and no call left to avoid. The
# qa_feature_analysis TOOL is untouched and still produces a report;
# it is chat-only.
async def _run_finalize(
    prepared: Any, all_cases: list, assumed_rows: list, progress: Any
) -> _SubFinal:
    category_results = [
        CategoryResult(category_name="Host Submission", cases=all_cases, error=None)
    ]
    captured: dict = {}

    def _on_ready(s) -> None:
        captured["suite"] = s
        if assumed_rows:
            try:
                s._assumed_artifacts = {"rows": assumed_rows}
            except Exception:
                logger.warning(
                    "Could not attach the Assumed Requirements rows", exc_info=True
                )

    async def _on_status(msg: str) -> None:
        await _common._emit(progress, msg)

    fa_skip_note = _fa_skip_note()
    summary, _x, _c, _t, status = await _finalize_generation(
        prepared,
        all_cases,
        category_results,
        defer_files=True,
        on_suite_ready=_on_ready,
        on_status=_on_status,
        ui_content=prepared.ui_content,
    )
    suite = captured.get("suite")
    return _SubFinal(suite, summary, status, fa_skip_note)


async def _finalize_submit(run: _SubRun, progress: Any) -> str | _SubFinal:
    prep_id = run.prep.prep_id
    dropped_note = run.gates.dropped_note
    conflict_note = run.read.conflict_note
    fin = await _run_finalize(
        run.prep.prepared, run.dup.all_cases, run.ground.assumed_rows, progress
    )
    suite = fin.suite
    if suite is None or not getattr(suite, "test_cases", None):
        await prep_store.delete_prep(prep_id)
        return (
            f"{dropped_note}{conflict_note}⚠️ The submitted suite "
            "produced no usable test cases after validation. Regenerate and "
            "resubmit."
        )
    return fin


# Batch 1, M1: the gate above measured the SUBMITTED cases on purpose --
# refusing a host for volume the SERVER itself then removed as content
# duplicates would punish work it was asked to do, and _dedupe_cases
# runs inside _finalize_generation. That leaves one gap: a host can
# clear the total by padding near-duplicates. (The final suite also
# shrinks from the grounding re-file and from an applied host duplicate
# review, which is why the heading says "in the FINAL suite" rather
# than blaming de-duplication alone.) So re-measure the FINAL
# suite and, only where the verdict would have been a refusal, add a
# WARNING -- never a refusal, because by here the suite is finalized and
# about to be exported, and the tester must not lose it over cases the
# server itself removed. Silent when the pre-dedup gate already spoke.
def _post_dedup_volume_note(
    meta: dict, suite: Any, prep_id: str, volume_note: str
) -> str:
    if not volume_note:
        _pmode, _pmd = prep_status._volume_floor_note(
            meta,
            list(getattr(suite, "test_cases", None) or []),
            prep_id,
            post_dedup=True,
        )
        if _pmode:
            volume_note = _pmd
    return volume_note


async def _mark_quality_refused(prep: _SubPrep) -> None:
    prep_id = prep.prep_id
    envelope = prep.envelope
    meta = prep.meta
    try:
        _mark = await prep_store.update_prep(
            prep_id,
            {**envelope, "meta": {**meta, "quality_refused": True}},
        )
        if (_mark or {}).get("error"):
            logger.warning(
                "prep %s not marked quality_refused (%s)",
                prep_id,
                (_mark or {}).get("error"),
            )
    except Exception:  # pragma: no cover - must never block a refusal
        logger.debug("quality_refused mark failed", exc_info=True)


async def _quality_refusal(
    prep: _SubPrep, quality: Any, q_kinds: list, first_beat: str
) -> str:
    prep_id = prep.prep_id
    version_note = prep.version_note
    _quality = quality
    _q_kinds = q_kinds
    _q_first_beat = first_beat
    await _mark_quality_refused(prep)
    await _common._audit(
        "mcp_submit_suite_refused",
        entity_id=prep_id,
        detail={
            "reason": "case_quality",
            "findings": len(_quality.findings),
            "kinds": _q_kinds,
        },
    )
    return (
        f"{version_note}⛔ **Submission refused:** the finished "
        "suite carries cases a tester cannot execute as written.\n\n"
        f"{case_quality_gate.gate_detail(_quality)}\n\n"
        "Fix those cases -- give every step concrete `test_data`, "
        "make each `expected_result` name the observable outcome "
        "instead of repeating the action, and give duplicated titles "
        "distinct behaviour -- then resubmit under the SAME prep_id "
        f"`{prep_id}`. **Nothing was discarded**: the prep and every "
        "staged category row survive, and nothing was exported or "
        "saved. To finalize as-is anyway, "
        "ask the tester first and then resend with "
        f"`quality_gate_ack=true`.{_q_first_beat}"
    )


# C2 (2026-08-30 audit): the submit-time CASE QUALITY gate. It reads the
# FINAL merged+renumbered suite rather than `all_cases`, because the
# duplicate-title signal only means anything after the merge and the
# tc_ids it prints must be the ids the tester reads in the export.
# Placed HERE -- after _finalize_generation, BEFORE the persist, the
# export and the finalized stamp -- so a refusal costs one
# round trip and destroys nothing: the prep and every staged category row
# survive and nothing is written.
# Two-beat, exactly like volume_floor_ack / step_assertion_ack: an ack on
# the FIRST submit is IGNORED and told so, because the tester cannot have
# seen these findings yet. A CLEAN suite yields no section and no
# refusal, so its reply stays byte-identical.
async def _quality_gate(
    prep: _SubPrep, suite: Any, acks: _SubAcks
) -> str | _SubQuality:
    prep_id = prep.prep_id
    meta = prep.meta
    quality_gate_ack = acks.quality_gate
    quality_note = ""
    _quality = case_quality_gate.scan_suite(
        list(getattr(suite, "test_cases", None) or [])
    )
    if _quality.found:
        _q_kinds = sorted(_quality.counts)
        _q_refused_before = bool(meta.get("quality_refused"))
        if not (quality_gate_ack and _q_refused_before):
            _q_first_beat = ""
            if quality_gate_ack and not _q_refused_before:
                _q_first_beat = (
                    "\n\n> ⚠️  `quality_gate_ack=true` arrived on "
                    "the FIRST submit for this prep and was IGNORED -- the "
                    "findings below had not been shown to anyone yet."
                )
            return await _quality_refusal(prep, _quality, _q_kinds, _q_first_beat)
        await _common._audit(
            "mcp_submit_suite_quality_override",
            entity_id=prep_id,
            detail={
                "reason": "quality_gate_ack",
                "findings": len(_quality.findings),
                "kinds": _q_kinds,
            },
        )
        quality_note = (
            "> ⚠️  Finalized with `quality_gate_ack=true` over "
            f"{len(_quality.findings)} case-quality finding(s) "
            f"({', '.join(_q_kinds)}). Those cases are in the suite and in "
            "the export AS SUBMITTED.\n\n"
        )
    return _SubQuality(quality_note)


# Piece 1: the bounded, deterministic duplicate-review block. Built AFTER
# finalize so each submitted tc_id resolves to the FINAL renumbered id via
# its content stable_id, and PREPENDED ahead of the variable-length summary
# (the same ordering rule that moved quality_section in front of
# checklist_section). Stays "" when the flag is OFF, so the reply for a
# submission WITHOUT the field is byte-identical to the pre-feature output.
# Every group the host sent is listed -- including any the safety screen
# refused to act on -- so a refusal is never silent.
def _duplicate_section_note(dup: _SubDup, submitted_cases: list, suite: Any) -> str:
    dup_note = dup.note
    if dup.review_on:
        dup_note = build_duplicate_section(
            dup.groups,
            submitted_cases,
            list(getattr(suite, "test_cases", None) or []),
            removed=dup.removed,
            applied=dup.apply,
            notes=dup.notes,
            agreements=dup.agreements,
        )
    return dup_note


# 2026-08-30 audit F3: `saved` was read for its suite_id and NOTHING
# else. save_suite never raises -- it returns the standard
# {"error": ..., "content": None} -- so a store failure fell straight
# through the `or suite.suite_id` fallback to the IN-MEMORY id, and the
# tester got a success-shaped reply (a suite_id, an .xlsx path, a case
# table) for a suite nothing had persisted. The damage is deferred:
# `qa_export_suite` and `qa_push_suite` both look the suite up by id and
# cannot find it, days later and with no clue why. Disclosed the same
# way a failed workbook write is disclosed in _auto_export_xlsx -- say
# what was lost, name what still works -- and PROTECTED in the reply
# below, because it states that the deliverable is not what it appears
# to be. "" on every healthy run, so those replies stay byte-identical.
# assemble_finalize_reply is a bare join -- each section owns
# its own separators or it runs into the next one's heading.
def _persist_failure_note(saved: dict, suite_id: str) -> str:
    persist_note = ""
    _persist_error = saved.get("error")
    if _persist_error:
        logger.warning("submit tail: save_suite failed -- %s", _persist_error)
        persist_note = (
            "> ⚠️  **This suite was NOT saved to the suite store** "
            f"({_persist_error}). The cases below are complete and any "
            "exported file was still written, but `"
            f"{suite_id}` is an in-memory id only -- `qa_export_suite` and "
            "`qa_push_suite` will NOT find this suite later. Keep the "
            "exported file, and re-submit if you need the suite stored."
            "\n\n"
        )
    return persist_note


# v1.97.0 cursor-hardening (item 3a/3b/6a/6b): computed BEFORE the
# save below overwrites the prior suite for this source -- the whole
# reason this must run here and not after.
# A2/N2 (2026-09-26, v1.98 scope A): supersede the single-finalizing-
# client stamp above with the UNION of every qa_submit_category
# contributor's own client plus this call's -- the laundering fix.
# `rows` is bound in all three finalize branches (Step 3).
# A4 (2026-09-26, v1.98 scope A): supersede the legacy single-client
# computation above with the SAME shared helper qa_submit_category
# uses (Step 2), built from the UNIONED _provenance above rather than
# only the finalizing client. The old block above still runs --
# harmless dead work, not worth a byte-for-byte rewrite of its
# escaped literal -- and is immediately overwritten here.
async def _stamp_provenance(suite: Any, rows: Any, source_url: Any) -> _SubProv:
    _dup_note, _dup_counts = await generate._duplicate_case_note(
        list(getattr(suite, "test_cases", None) or []), source_url
    )
    _provenance = _elicit._dispatch_provenance()
    _provenance = _elicit._union_provenance(rows)
    suite._duplicate_case_note = _dup_note
    suite._duplicate_case_counts = _dup_counts
    suite._provenance = _provenance
    _session_provenance_note = (
        ""
        if _provenance.get("known_editor")
        else (
            "> ⚠️ This suite was saved by an MCP client this "
            "server does not recognise (`"
            + str(_provenance.get("client_name") or "unknown")
            + "`). If you did not expect that, a process may be calling "
            "this server's tools outside your editor's own MCP session."
        )
    )
    _session_provenance_note = _elicit._unrecognized_client_note(
        _provenance, context="suite"
    )
    return _SubProv(_dup_note, _dup_counts, _provenance, _session_provenance_note)


# FINALIZE branch: replicate the server persistence tail
# (handle_generate_test_cases ~lines 1470-1533), then delete the prep.
# ops-7: with the 108s advisory-gap call gone, THIS tail is the largest
# remaining server-side cost on a submit -- and it logged nothing. A real
# run on 2026-07-29 spent 43 seconds between finalize (25ms) and
# suite_store, with no way to tell which await it was. Time each step.
# F1c: a retried finalize for this SAME prep must converge on the
# suite this prep already produced, not fork a second one.
async def _save_submitted_suite(
    prep: _SubPrep, rows: Any, suite: Any, progress: Any
) -> _SubSaved:
    prep_id = prep.prep_id
    source_text = prep.source_text
    source_url = prep.source_url
    _t0 = time.monotonic()
    await _common._emit(progress, "\U0001f4be Saving the suite…")
    _t_emit = time.monotonic()
    prov = await _stamp_provenance(suite, rows, source_url)
    _provenance = prov.provenance
    saved = await save_suite(
        suite,
        feature_text=source_text,
        source_url=source_url,
        prep_id=prep_id,
        provenance=_provenance,
    )
    logger.info(
        "submit tail: progress emit %.1fs | save_suite %.1fs",
        _t_emit - _t0,
        time.monotonic() - _t_emit,
    )
    suite_id = (saved.get("content") or {}).get("suite_id", "") or suite.suite_id
    persist_note = _persist_failure_note(saved, suite_id)
    return _SubSaved(suite_id, persist_note, prov)


# F5/F6/F7 (2026-08-15): carry this run's SHORTFALLS into the workbook.
# Every one of these was already disclosed in the reply, and every one
# was measured surviving into a finalized, exported suite anyway: the
# reply is transient chat that a summarising host model prunes, while
# the .xlsx is what the tester keeps, attaches to the ticket and reads
# back a week later. Attached HERE -- after finalize, before
# _auto_export_xlsx below -- because only here are all three verdicts
# known and the suite object still the one about to be written.
# Best-effort: a suite with nothing to report gets no sheet at all, and
# a failure here must never cost the tester the export.
async def _attach_generation_notes(
    run: _SubRun, fin: _SubFinal, saved: _SubSaved
) -> None:
    meta = run.prep.meta
    source_text = run.prep.source_text
    suite = fin.suite
    _dup_note = saved.prov.dup_case_note
    _provenance = saved.prov.provenance
    try:
        _gen_notes = await submit_category._early_gen_notes(
            meta, _dup_note, source_text, _provenance, suite
        )
        _gen_notes += submit_category._checklist_gap_gen_notes(run.checklist.gap)
        _gen_notes += submit_category._ambiguity_gen_notes(run.amb.result)
        _gen_notes += submit_category._step_assertion_gen_notes(
            run.gates.assert_findings, run.gates.data_findings
        )
        _gen_notes += submit_category._quality_advisory_gen_notes(
            run.dup.all_cases, source_text, fin.summary
        )
        if _gen_notes:
            suite._generation_notes = _gen_notes
    except Exception:  # pragma: no cover - defensive; must never block export
        logger.debug("generation notes attachment failed", exc_info=True)


async def _persist_artifacts(prep: _SubPrep, saved: _SubSaved, suite: Any) -> None:
    source_text = prep.source_text
    source_url = prep.source_url
    suite_id = saved.suite_id
    _checklist_artifacts = getattr(suite, "_checklist_artifacts", None)
    if _checklist_artifacts and suite_id:
        await save_checklist(suite_id, _checklist_artifacts)
    _t_corpus = time.monotonic()
    await generate._persist_suite_to_corpus(
        suite, feature_text=source_text, source_url=(source_url or "")
    )
    logger.info("submit tail: corpus persist %.1fs", time.monotonic() - _t_corpus)


# Counts ONLY, and only when the AC job actually ran, so a
# flag-OFF run's audit row is byte-identical to today's.
# FIX 2: "absent" alone made a FORFEITED safety gate read
# identically to "checked, found nothing" for anyone
# reading audit.db (row 99, 2026-08-09). Record whether
# the preflight verdict was READABLE at all, and whether
# the tester was actually TOLD. Both keys sit inside the
# existing `amb_result is not None` conditional, so a prep
# that shipped no ambiguity job contributes no keys and
# its row stays byte-identical.
#
# host_ambiguity_ran is also the FORFEIT-RATE signal an
# operator needs: with QA_HOST_AMBIGUITY_REVIEW_ENABLED
# hardcoded ON the server-side gate is skipped outright
# (see the "ambiguity gate SKIPPED" branch above), so
# ran=False means NO screening happened anywhere. Surfacing
# that rate in qa-doctor / the runbook is a filed
# follow-up, not part of this change.
# Batch 4 LAYER 3: the image FORFEIT-RATE signal, shaped exactly
# like the ambiguity pair above and for the same reason. Until
# now a submit row recorded only the PREPARE-side "we asked"
# stamp, so an operator reading audit.db could not tell "the
# screens were judged and matched" from "nothing came back and
# nobody was told" -- which is precisely the run that started
# this batch. `host_image_relevance_ran` is whether any USABLE
# verdict returned (never HostImageResult.ran, which only means
# a usable DESCRIPTION returned), `host_image_off_topic` counts
# hard `no` verdicts ONLY, and `host_image_disclosed` records
# whether the tester actually SAW the off-topic warning. Counts
# and booleans only, never the claimed content. Both keys sit
# inside a conditional on this prep having asked for a verdict,
# so a prep that shipped no image job -- or an OLD envelope --
# contributes no keys and its row stays byte-identical.
# Review L1: "disclosed" must mean the tester was
# WARNED, in EITHER form -- the off-topic block or the
# "no usable verdict came back" note. Keyed on the
# off-topic list alone it read False on the
# ZERO-VERDICT run, i.e. a forfeit read exactly like a
# pass, which is the failure this signal exists to end.
def _audit_review_detail(run: _SubRun) -> dict:
    meta = run.prep.meta
    ac_result = run.ac.result
    amb_result = run.amb.result
    amb_note = run.amb.note
    img_note = run.images.note
    _img_result = run.images.result
    _img_counts = run.images.counts
    return {
        **(
            {
                "host_acs": len(ac_result.acs),
                "host_acs_dropped": ac_result.dropped,
            }
            if ac_result is not None
            else {}
        ),
        **(
            {
                "host_ambiguity_severity": (amb_result.severity or "absent"),
                "host_ambiguity_ran": bool(amb_result.ran),
                "host_ambiguity_disclosed": bool(amb_note),
            }
            if amb_result is not None
            else {}
        ),
        **(
            {
                "host_image_relevance_ran": bool(_img_counts.get("ran")),
                "host_image_off_topic": int(_img_counts.get("no") or 0),
                "host_image_disclosed": bool(
                    img_note
                    and (
                        getattr(_img_result, "off_topic", None)
                        or not _img_counts.get("ran")
                    )
                ),
            }
            if _img_result is not None and meta.get("host_image_relevance")
            else {}
        ),
    }


# `cases` is the POST-removal count, so without these two the trail
# cannot tell "the host generated 6 cases" from "the host removed
# 58". dedup_groups counts what was SUBMITTED (before the safety
# screen); dedup_removed counts what was actually deleted.
# Whether the field was OFFERED at all -- the signal the runbook
# gate asks an operator to check, and not derivable from a zero
# dedup_groups count.
def _submit_audit_detail(
    run: _SubRun, status: Any, case_count: int, suite: Any
) -> dict:
    parsed = run.read.parsed
    return {
        "status": status,
        "cases": case_count,
        "dedup_groups": run.dup.groups_submitted,
        "dedup_removed": len(run.dup.removed),
        **_audit_review_detail(run),
        **prep_status._rtm_trace_detail(suite),
        "dedup_offered": bool(getattr(parsed, "duplicate_review_offered", False)),
    }


async def _record_submit(run: _SubRun, fin: _SubFinal, saved: _SubSaved) -> None:
    suite = fin.suite
    suite_id = saved.suite_id
    case_count = len(getattr(suite, "test_cases", []) or [])
    telemetry.add_tool_properties(case_count=case_count, source="host")
    await _common._audit(
        "mcp_submit_suite",
        entity_id=suite_id or None,
        detail=_submit_audit_detail(run, fin.status, case_count, suite),
    )


class _SubExport(NamedTuple):
    note: str
    paths: list
    auto_export: bool


class _SubTail(NamedTuple):
    prescreen_head: str
    prescreen_pairs: str
    rtm_note: str
    cov_signal_note: str


# F03: result_md is shaped at the RETURN now, not here -- its summary
# budget depends on export_note, rtm_note and cov_signal_note, none of
# which exist yet at this point. Nothing between here and there reads
# it.
# FRONT-loaded, not appended (see _auto_export_xlsx): export_note goes
# near the HEAD of the returned string below, ahead of every OTHER note
# and the suite body, so a paraphrasing host model cannot drop the
# deliverable.
#
# PRECEDENCE, 2026-08-09 (Batch 3, FIX 2, review M2): exactly ONE thing
# now outranks it -- {amb_note}, the boomeranged TICKET-7154 preflight
# verdict, whose own builder documents itself as "Emitted FIRST, ahead of
# every other section, because it is the one thing that can invalidate
# everything under it" (agents/host_jobs.build_ambiguity_result_section).
# Two "must be first" contracts cannot both hold, so this one yields and
# SAYS SO rather than leaving a comment that asserts a falsehood: handing
# a tester a file path for a suite that carries NO ambiguity screening is
# precisely the over-claim, so the screening LOSS takes first position and
# the deliverable takes second. This note's own purpose is unharmed -- it
# is still ahead of every other note and the suite body -- and {amb_note}
# is "" for any prep that shipped no ambiguity job, so on those replies
# export_note is still literally first.
async def _export_submitted_suite(
    prep: _SubPrep,
    suite: Any,
    ask_text: Any,
    progress: Any,
) -> _SubExport:
    source_text = prep.source_text
    source_url = prep.source_url
    auto_export = bool(getattr(suite, "test_cases", None))
    xlsx_paths: list[str] = []
    export_note = ""
    if auto_export:
        export_note = await export._auto_export_xlsx(
            suite,
            ask_text=ask_text,
            source_url=source_url or source_text,
            on_path=xlsx_paths.append,
            progress=progress,
        )
        if export_note:
            export_note += "\n\n---\n\n"
    return _SubExport(export_note, xlsx_paths, auto_export)


# D4 (2026-08-21): the server's OWN duplicate prescreen, on the MERGED
# finalize path. The measure already existed -- host_mode's F08
# title-token Jaccard -- but `_dup_shortlist_note` wires it to
# `qa_submit_category` ONLY, and only when that submission completes the
# expected set. The TICKET-5646 run finalized through `qa_submit_suite`,
# so the prescreen never ran and the host's empty `duplicate_groups`
# ("review ran, none found") stood unchallenged over a suite with nine
# redundant clusters. Same measure, same constants, second call site.
#
# THREE things about the placement are deliberate:
#   * AFTER the quality-gate early return above. That return is ONE OF
#     SEVERAL fix-and-resubmit refusals in this function -- the
#     step_assertion refusal and the PrepSerdeError reply are the same
#     kind -- and every one of them is left untouched, so this cannot
#     repeat on every resubmit.
#   * AFTER _finalize_generation, which renumbers every tc_id, so the
#     ids printed are the FINAL ones the workbook carries.
#   * Gated on `_review_claimed_none`. With no review reported,
#     dup_status_note above ALREADY says "No duplicate review ran ...
#     Any cross-category duplicates are still present" -- there is no
#     false assurance to contradict, so this section would be noise.
#     When the host DID name groups, build_duplicate_section reports
#     them pair by pair and a second advisory list would be noise too.
#
# Split in TWO on purpose: the CLAIM (dup_prescreen_head) is a protected
# reply section because it states that the assurance above is false,
# while the EVIDENCE (dup_prescreen_pairs) is trimmable -- every pair is
# a tc_id and a title, both printed in the workbook this same reply just
# handed over. See the ReplySection rows below and the reply-budget
# section of .claude/plans/plan-d4-d5-TICKET5646-2026-08-21.md.
# A disclosure must never be able to break a finalize.
def _dup_prescreen_notes(suite: Any, parsed: Any, dup: _SubDup) -> tuple[str, str]:
    dup_review_on = dup.review_on
    dup_groups = dup.groups
    dup_prescreen_head = ""
    dup_prescreen_pairs = ""
    _review_claimed_none = bool(getattr(parsed, "duplicate_review_offered", False))
    try:
        if dup_review_on and not dup_groups and _review_claimed_none:
            _pre_pairs, _pre_total = build_dup_shortlist_counted(
                dup_shortlist_cases_json(list(getattr(suite, "test_cases", None) or []))
            )
            dup_prescreen_head = build_dup_contradiction_headline(
                len(_pre_pairs), _pre_total
            )
            if dup_prescreen_head:
                dup_prescreen_pairs = build_dup_contradiction_pairs(_pre_pairs)
    except Exception:
        logger.debug("merged-path duplicate prescreen failed", exc_info=True)
    return dup_prescreen_head, dup_prescreen_pairs


# Fix 5b: STAMP instead of DELETE. Deleting made "prep gone" mean three
# different things at once, so a resubmit after a SUCCESSFUL finalize was
# told to re-prepare -- i.e. to regenerate a suite that already existed.
# Only the success path is stamped: the failure branch above ("produced no
# usable test cases") still DELETES, because stamping a finalized_suite_id
# there would record a suite that was never created.
# Fall back to today's behaviour rather than leave an unstamped prep
# live: an unstamped prep would be offered as resumable.
async def _stamp_prep_finalized(prep: _SubPrep, suite: Any, xlsx_paths: list) -> None:
    prep_id = prep.prep_id
    envelope = prep.envelope
    _final_stamp = {
        "suite_id": str(getattr(suite, "suite_id", "") or ""),
        "export_path": str(xlsx_paths[0]) if xlsx_paths else "",
    }
    _stamped = await prep_store.update_prep(
        prep_id, {**envelope, prep_store.FINALIZED_KEY: _final_stamp}
    )
    if _stamped.get("error"):
        logger.warning(
            "prep %s could not be stamped finalized (%s) - deleting instead",
            prep_id,
            _stamped.get("error"),
        )
        await prep_store.delete_prep(prep_id)


# F03 (2026-08-16): the slot ORDER below is unchanged -- it is the
# precedence the two comment blocks above argue for. What changed is
# that each slot now carries a name and, where it is droppable, a
# reason; the reply as a WHOLE is bounded instead of only the
# `summary` inside result_md; and the summary yields to the
# disclosures rather than the other way round. Under budget
# assemble_finalize_reply returns the identical concatenation.
#
# PROTECTED means "this section states whether the deliverable is
# what it appears to be". Each one below was read against its own
# builder before being ranked -- cited by SYMBOL, because line
# numbers in this file rot (a concurrent batch moved every one of
# them by ~290 lines while this very plan was under review):
# _dropped_note is documented as never-swallowed, and
# _no_coverage_signal_note as always-on because its ABSENCE would
# read as "coverage was checked"; img_note because the _audit call
# above stamps host_image_disclosed from it BEFORE this assembly
# runs; dup_note because it reports cases the server REMOVED;
# conflict_note because it reports staged rows that did NOT make it
# in; version_note because a prep staged on a different install was
# finalized under different flags.
#
# Row comments, in row order:
# A4 (2026-09-26, v1.98 scope A): moved up to SECOND -- an
# unrecognised-client warning buried 7th is a warning nobody
# reads. It does NOT take the first slot: L3
# (test_the_submit_reply_puts_the_ambiguity_note_first) pins the
# ambiguity note there, because a host model summarising the reply
# keeps the deliverable path and drops whatever follows it. Second
# is the highest slot available without breaking that invariant.
# See this section's old position below for the comment explaining
# what computes _session_provenance_note.
#
# PROTECTED for the same reason volume_note is: this line is the
# only place the finalize reply says the tester waved 65
# unfalsifiable steps through. Accepting an ack silently is the
# F2 pattern this whole batch exists to correct.
#
# PROTECTED for the same reason: this line is the only place the
# finalize reply says the tester waved through cases whose steps
# name no data, restate their own action, or repeat another case's
# title. "" on every clean run, so those replies stay byte-identical.
#
# F3: ahead of the Excel path for the reason the comment block
# above the return gives -- a host model summarising this reply
# keeps the path and drops what follows it, and "your suite was
# not stored" must not be what gets dropped.
#
# v1.97.0 cursor-hardening (item 6b) / A4 (2026-09-26, v1.98
# scope A): this section MOVED to the front of `_sections` --
# see above -- so the warning is not buried 7th. Comment kept
# here as the historical note for _session_provenance_note's
# computation earlier in this function.
#
# Provenance of a field, not a claim about the suite: it says
# the category labels are self-reported. Re-derivable by
# submitting per category.
#
# Names a tool the tester can call on demand, which is the
# whole content of the note.
#
# The coverage REPORT (not the gap warning, which is protected
# above): a measurement section re-derivable from the suite.
#
# v1.97.0 cursor-hardening (item 3a): a WARN-only, hash-based
# check against the prior suite for this same source (and,
# best-effort, the RAG corpus) -- distinct from the intra-suite
# `dup_note` above, which compares cases WITHIN this one submit.
#
# D4: PROTECTED, because it states that the duplicate-review
# assurance immediately above it is CONTRADICTED -- a claim
# about whether the deliverable is what it appears to be, which
# is the definition _omission_marker leans on when it promises
# "every notice about whether this suite is VALID is still
# above". Dropping it would leave the false assurance standing
# alone, which is the exact D4 defect. It is bounded at 496
# chars by construction, so protecting it is cheap.
#
# D4: TRIMMABLE at _REPLY_P_EXPORTED, whose stated reason is
# "the same disclosure is written into the export" -- here
# literally true, since every pair is a tc_id and a title and
# both are printed in the workbook this reply just handed
# over. NOTE THE ACTUAL DROP ORDER: assemble_finalize_reply
# sorts (priority, INDEX), so this row does NOT go first --
# the checklist NLI tier note shares this priority and sits
# at a lower index, so it yields ahead of this list. That is
# intended rather than tolerated: that note says of itself
# that the same disclosure is written into the checklist
# coverage notes, so it is FULLY reproduced in the export,
# while these pairs are only reconstructible from it. If the
# budget takes this one too, _omission_marker names it AND
# the protected headline above still carries the
# contradiction and the count, so nothing false is left
# standing. Marking this protected instead is what put the
# first attempt at this fix over the cap;
# tests/test_dup_prescreen_merged_submit.py pins the split.
def _notice_sections(
    run: _SubRun, post: _SubPost, saved: _SubSaved, export: _SubExport
) -> list:
    amb_note = run.amb.note
    export_note = export.note
    checklist_note, checklist_gap = run.checklist.note, run.checklist.gap
    return [
        _reply.ReplySection("ambiguity screening", amb_note, protected=True),
        _reply.ReplySection(
            "session provenance", saved.prov.session_note, protected=True
        ),
        _reply.ReplySection("volume floor", post.volume_note, protected=True),
        _reply.ReplySection(
            "step assertions", run.gates.assertion_note, protected=True
        ),
        _reply.ReplySection("case quality", post.quality_note, protected=True),
        _reply.ReplySection(
            "checklist gap",
            checklist_note if checklist_gap else "",
            protected=True,
        ),
        _reply.ReplySection("suite persistence", saved.persist_note, protected=True),
        _reply.ReplySection("Excel export", export_note, protected=True),
        _reply.ReplySection("server version", run.prep.version_note, protected=True),
        _reply.ReplySection("dropped cases", run.gates.dropped_note, protected=True),
    ]


def _provenance_sections(run: _SubRun, post: _SubPost) -> list:
    checklist_note, checklist_gap = run.checklist.note, run.checklist.gap
    return [
        _reply.ReplySection(
            "unused staged rows", run.read.conflict_note, protected=True
        ),
        _reply.ReplySection(
            "category provenance", run.cases.cat_source, _reply._REPLY_P_PROVENANCE
        ),
        _reply.ReplySection(
            "Feature Analysis pointer", post.fin.fa_skip_note, _reply._REPLY_P_POINTER
        ),
        _reply.ReplySection("acceptance criteria", run.ac.note, protected=True),
        _reply.ReplySection("grounding", run.ground.note, protected=True),
        _reply.ReplySection(
            "checklist coverage",
            "" if checklist_gap else checklist_note,
            _reply._REPLY_P_REPORT,
        ),
    ]


def _dup_and_tail_sections(
    run: _SubRun, post: _SubPost, saved: _SubSaved, tail: _SubTail
) -> list:
    return [
        _reply.ReplySection("screenshots", run.images.note, protected=True),
        _reply.ReplySection("duplicate review", run.dup.status_note, protected=True),
        _reply.ReplySection("duplicates", post.dup_note, protected=True),
        _reply.ReplySection(
            "duplicate cases", saved.prov.dup_case_note, protected=True
        ),
        _reply.ReplySection("duplicate prescreen", tail.prescreen_head, protected=True),
        _reply.ReplySection(
            "duplicate prescreen pairs", tail.prescreen_pairs, _reply._REPLY_P_EXPORTED
        ),
        _reply.ReplySection("traceability orphans", tail.rtm_note, protected=True),
        _reply.ReplySection("coverage signal", tail.cov_signal_note, protected=True),
    ]


def _reply_sections(
    run: _SubRun,
    post: _SubPost,
    saved: _SubSaved,
    export: _SubExport,
    tail: _SubTail,
) -> list:
    return [
        *_notice_sections(run, post, saved, export),
        *_provenance_sections(run, post),
        *_dup_and_tail_sections(run, post, saved, tail),
    ]


# Two shaping passes, both pure and both cheap: the first measures
# the header the summary sits under, so the budget is exact rather
# than a reserved guess.
# FIX 2 (2026-08-09): {amb_note} LEADS. host_mode's
# build_ambiguity_result_section documents itself as "Emitted FIRST,
# ahead of every other section, because it is the one thing that can
# invalidate everything under it" -- but it was landing SEVENTH,
# behind the .xlsx path a tester reads as the deliverable, so a host
# summarising this reply kept the path and dropped the caveat. "" for
# any prep that shipped no ambiguity job, so those replies stay
# byte-identical. The precedence against export_note's own
# FRONT-loading contract is recorded where that contract is stated.
# F5/F6 (2026-08-15): {volume_note} and a MISSING checklist now sit
# ahead of {export_note}, joining {amb_note} in the leading caveat
# band. Same reasoning that hoisted {amb_note} on 2026-08-09 and the
# same measured failure: a host model summarising this reply keeps
# the .xlsx path -- the thing that looks like the deliverable -- and
# drops what follows it. An under-generated suite and a suite with
# no requirements checklist at all are both claims about whether
# the deliverable is what it appears to be, so they must not sit
# behind it. Both are "" on a healthy run, so those replies keep
# today's exact ordering. A checklist note that is NOT a gap
# (present, or carried forward from an earlier round) stays in the
# tail with the other informational sections.
def _submit_reply(
    run: _SubRun,
    post: _SubPost,
    saved: _SubSaved,
    export: _SubExport,
    tail: _SubTail,
) -> str:
    suite = post.fin.suite
    suite_id = saved.suite_id
    status = post.fin.status
    summary = post.fin.summary
    auto_export = export.auto_export
    _sections = _reply_sections(run, post, saved, export, tail)
    _head = _reply.shape_generation_result(
        "", suite, suite_id, status, auto_export=auto_export
    )
    result_md = _reply.shape_generation_result(
        summary,
        suite,
        suite_id,
        status,
        auto_export=auto_export,
        summary_cap=_reply.summary_budget(_sections, len(_head)),
    )
    return _reply.assemble_finalize_reply(
        _sections + [_reply.ReplySection("suite", result_md, protected=True)]
    )


async def handle_submit_suite(
    prep_id: str,
    suite_json,
    *,
    volume_floor_ack: bool = False,
    image_relevance_ack: bool = False,
    step_assertion_ack: bool = False,
    quality_gate_ack: bool = False,
    ask_text: _elicit.AskCb = None,
    progress: _common.ProgressCb = None,
) -> str:
    """BACK half of host-mode generation: validate the host-generated suite,
    finalize it deterministically, and return EITHER a refusal to fix and
    resubmit (SAME prep_id) OR the finished suite + export path. Never raises."""
    dispatch_guard.require_dispatched("qa_submit_suite")
    # UNTRUSTED: the host-sent id is echoed in replies, so it is sanitised
    # HERE, once, rather than at every interpolation downstream.
    prep_id = safe_prep_id(prep_id)
    if not prep_id:
        return (
            "⚠️ Missing prep_id. Run `qa_prepare_test_cases` first, then "
            "submit with the prep_id it returned."
        )
    try:
        prep = await _load_submit_prep(prep_id)
        if isinstance(prep, str):
            return prep
        acks = _SubAcks(
            volume_floor_ack,
            image_relevance_ack,
            step_assertion_ack,
            quality_gate_ack,
        )
        return await _submit_loaded_prep(
            prep_id, prep, suite_json, acks, (ask_text, progress)
        )
    except Exception as exc:
        logger.exception("handle_submit_suite failed")
        _common._capture_error(exc, "qa_submit_suite")
        return f"⚠️ Submitting the suite failed: {exc}"


async def _submit_loaded_prep(
    prep_id: str,
    prep: Any,
    suite_json: Any,
    acks: Any,
    callbacks: tuple[_elicit.AskCb, _common.ProgressCb],
) -> str:
    """Run a loaded prep through review, finalize, quality gate and publish.

    ``callbacks`` is the (ask_text, progress) pair of the tool call."""
    ask_text, progress = callbacks
    run = await _build_submit_run(prep_id, prep, suite_json, acks)
    if isinstance(run, str):
        return run
    fin = await _finalize_submit(run, progress)
    if isinstance(fin, str):
        return fin
    volume_note = _post_dedup_volume_note(
        prep.meta, fin.suite, prep_id, run.gates.volume_note
    )
    quality = await _quality_gate(prep, fin.suite, acks)
    if isinstance(quality, str):
        return quality
    post = _SubPost(
        fin,
        volume_note,
        quality.note,
        _duplicate_section_note(run.dup, run.cases.submitted_cases, fin.suite),
    )
    return await _publish_submitted_suite(run, post, ask_text, progress)


async def _build_submit_run(
    prep_id: str, prep: Any, suite_json: Any, acks: Any
) -> "_SubRun | str":
    """Read, gate and review the submission; a str return is a ready refusal."""
    read = await _read_submission(
        prep_id, prep.envelope, prep.meta, suite_json, _has_full_submission(suite_json)
    )
    if isinstance(read, str):
        return read
    cases = await _case_intake(prep_id, read)
    gates = await _submit_gates(prep, cases, read.parsed, acks)
    if isinstance(gates, str):
        return gates
    amb = await _ambiguity_review(prep_id, prep.meta, read.parsed, cases.staged_rows)
    if isinstance(amb, str):
        return amb
    ac = _ac_review(prep.meta, read.parsed, prep.prepared, cases.all_cases)
    ground = _grounding_review(read.parsed, cases.all_cases)
    checklist = _checklist_review(prep.meta, read.parsed, prep.prepared)
    images = await _image_review(prep, read.parsed, cases.staged_rows, acks)
    if isinstance(images, str):
        return images
    dup = _dup_review(read.parsed, read.has_full, ground.all_cases)
    return _SubRun(prep, read, cases, gates, amb, ac, ground, checklist, images, dup)


async def _publish_submitted_suite(
    run: "_SubRun",
    post: "_SubPost",
    ask_text: _elicit.AskCb,
    progress: _common.ProgressCb,
) -> str:
    """Save, persist and export the finished suite, then build the reply."""
    prep, fin = run.prep, post.fin
    saved = await _save_submitted_suite(prep, run.read.rows, fin.suite, progress)
    await _attach_generation_notes(run, fin, saved)
    await _persist_artifacts(prep, saved, fin.suite)
    await _record_submit(run, fin, saved)
    export = await _export_submitted_suite(prep, fin.suite, ask_text, progress)
    # Batch C items 1 + 4: two NOTES, never refusals, computed once HERE so
    # both finalize routes (Path A rows, Path B merged suite_json) are covered.
    # Not on the fix-and-resubmit early returns: the suite is not final there.
    # No coverage view is built, so the coverage-signal note always fires.
    tail = _SubTail(
        *_dup_prescreen_notes(fin.suite, run.read.parsed, run.dup),
        prep_status._rtm_orphan_note(fin.suite),
        prep_status._no_coverage_signal_note(None),
    )
    await _stamp_prep_finalized(prep, fin.suite, export.paths)
    return _submit_reply(run, post, saved, export, tail)


_PUSH_TARGETS = ("testrail", "xray")


def _push_suite_refusal(target: str, apply: bool, enabled: bool, flag_name: str) -> str:
    """The refusal text for an unknown target or a flag-OFF real push, else ""."""
    from tools.untrusted import single_line as _safe

    if target not in _PUSH_TARGETS:
        return (
            "⚠️ Unknown push target "
            + repr(_safe(target, 40))
            + ". Choose one of: "
            + ", ".join(_PUSH_TARGETS)
            + "."
        )
    if apply and not enabled:
        return (
            "⚠️ **Nothing was sent.** A real push to "
            + target
            + " needs `"
            + flag_name
            + "=true` in `.env` and an MCP server restart. Re-run with `apply=false` "
            "for a preview of exactly what would be created."
        )
    return ""


@dataclass(frozen=True)
class _PushRequest:
    """What one qa_push_suite call asks for, after normalisation."""

    suite_id: str
    target: str
    apply: bool
    project_id: Any = 0
    section_name: str = ""


async def _push_suite_cases(
    suite: Any, req: _PushRequest, progress: _common.ProgressCb
) -> "dict | str":
    """Run the pusher for ``req.target``; a str return is a ready reply, not a result."""
    from tools.untrusted import single_line as _safe

    suite_id, target, apply = req.suite_id, req.target, req.apply
    section_name = req.section_name
    verb = "Pushing" if apply else "Previewing"
    if target != "testrail":
        from tools import xray_pusher

        await _common._emit(progress, "\U0001f4e4 " + verb + " to Xray…")
        return await xray_pusher.push_suite(suite, dry_run=not apply)
    try:
        project_id = int(req.project_id or 0)
    except (TypeError, ValueError, OverflowError):
        project_id = 0
    if project_id <= 0:
        return (
            "⚠️ TestRail needs the numeric project id: "
            '`qa_push_suite(suite_id="'
            + _safe(suite_id, 80)
            + '", target="testrail", project_id=<id>)`. '
            "It is the number in the TestRail URL for that project."
        )
    from tools import testrail_pusher

    await _common._emit(progress, "\U0001f4e4 " + verb + " to TestRail…")
    return await testrail_pusher.push_suite(
        suite,
        project_id,
        section_name=(section_name or "QA Assistant Export"),
        dry_run=not apply,
    )


def _push_suite_reply(
    content: dict,
    req: _PushRequest,
    *,
    enabled: bool,
    flag_name: str,
    total: int,
) -> str:
    """Format the preview or the pushed reply, with the batch-cap note."""
    pushed = int(content.get("pushed") or 0)
    skipped = int(content.get("skipped") or 0)
    would = int(content.get("would_push") or 0)
    # The pushers cap the case list BEFORE counting, so `would_push`/`pushed` can
    # never reveal the truncation on their own -- compare against the suite.
    counted = would if not req.apply else pushed + skipped
    cap_note = _push_cap_note(counted, total)
    if not req.apply:
        return _push_preview_text(content, req, would, (enabled, flag_name)) + cap_note
    return _push_done_text(content, req.target, pushed, skipped) + cap_note


def _push_cap_note(counted: int, total: int) -> str:
    """The "pusher capped the batch" warning, or "" when nothing was cut."""
    if not (total and counted and counted < total):
        return ""
    return (
        "\n\n⚠️ Only the first "
        + str(counted)
        + " of "
        + str(total)
        + " cases were included — this pusher caps the batch. The rest were "
        "NOT sent."
    )


def _push_preview_text(
    content: dict, req: _PushRequest, would: int, flag: tuple[bool, str]
) -> str:
    """The dry-run reply body (before the cap note)."""
    from tools.untrusted import single_line as _safe

    enabled, flag_name = flag
    return (
        "## Push preview — "
        + req.target
        + "\n\n**Nothing was sent.** "
        + str(would)
        + " case(s) from suite `"
        + _safe(req.suite_id, 80)
        + "` would be created"
        + (
            " under section '" + _safe(content.get("section_name"), 60) + "'"
            if content.get("section_name")
            else ""
        )
        + ".\n\nRe-run with `apply=true` to push for real"
        + ("" if enabled else " (needs `" + flag_name + "=true` first)")
        + "."
    )


def _push_done_text(content: dict, target: str, pushed: int, skipped: int) -> str:
    """The real-push reply body (before the cap note)."""
    from tools.untrusted import single_line as _safe

    created = content.get("case_ids") or content.get("issue_keys") or []
    return (
        "✅ Pushed **"
        + str(pushed)
        + "** case(s) to "
        + target
        + (" (" + str(skipped) + " skipped)" if skipped else "")
        + ".\n\n"
        + (
            "- created: `" + _safe(", ".join(str(c) for c in created[:20]), 300) + "`\n"
            if created
            else ""
        )
        + "\nThis left your organisation and **nothing here deletes those cases — "
        "remove them in " + target + " if this was a mistake.**"
    )


# On the `_safe` import inside `handle_push_suite`. Re-pointed 2026-09-02: it
# used to be `from agents.api_test_agent import _safe`, outside any try. The
# sanitiser now lives in tools/untrusted.py, where it belongs and which the
# dist copies.
#
# CORRECTED after review: the first version of this comment said the old
# import would break a public-distribution install -- "a handler the dist
# CAN register, both push kill-switches are ordinary settings". FALSE.
# qa_push_suite registers inside `if not _test_cases_only()` and the dist is
# test-cases-only, so this handler never registers there and this
# function-local import never runs. Recorded because a wrong reason in the
# tree is worse than no reason: the next reader trusts it.
async def handle_push_suite(
    suite_id: str,
    target: str,
    project_id: int = 0,
    section_name: str = "",
    apply: bool = False,
    *,
    progress: _common.ProgressCb = None,
) -> str:
    """PUSH a stored suite into TestRail or Xray. Never raises.

    This is the ONLY credentialed outbound WRITE in the tree, which is why it has
    two independent guards: the target's kill-switch setting AND ``apply=true`` on
    the call. Either one missing means no request is made.

    A dry run (the default) needs no flag HERE: it reports what WOULD be created
    and makes no request at all. The kill-switch nonetheless gates tool
    REGISTRATION in ``mcp_server``, so with both push flags OFF this tool is not
    offered at all and the flagless preview is reachable only once one of them is
    ON. A flag-OFF call with ``apply=true`` REFUSES BY NAME
    rather than quietly downgrading to a dry run -- a tester who asked to push and
    got a success-shaped reply would believe cases exist in a tracker that do not.

    Nothing here deletes a remote case afterwards, so a successful push says so.
    """
    from tools.untrusted import single_line as _safe

    target = (target or "").strip().lower()
    flag_name = (
        "QA_TESTRAIL_PUSH_ENABLED" if target == "testrail" else "QA_XRAY_PUSH_ENABLED"
    )
    enabled = bool(
        settings.qa_testrail_push_enabled
        if target == "testrail"
        else settings.qa_xray_push_enabled
    )
    refusal = _push_suite_refusal(target, apply, enabled, flag_name)
    if refusal:
        return refusal
    suite_id = (suite_id or "").strip()
    if not suite_id:
        return await _elicit._recent_suites_markdown("qa_push_suite")
    req = _PushRequest(suite_id, target, apply, project_id, section_name)
    try:
        return await _push_suite_run(req, progress, enabled, flag_name)
    except Exception as exc:  # never-raise contract
        logger.exception("mcp push_suite failed")
        return "\u26a0\ufe0f Push to " + target + " failed: " + _safe(str(exc), 200)


async def _push_suite_run(
    req: _PushRequest, progress: _common.ProgressCb, enabled: bool, flag_name: str
) -> str:
    """Load the suite, push it, audit it and render the reply (may raise)."""
    from tools.untrusted import single_line as _safe

    suite_id = req.suite_id
    loaded = await load_suite(suite_id)
    if loaded.get("error"):
        return (
            "\u26a0\ufe0f Could not load suite `"
            + _safe(suite_id, 80)
            + "`: "
            + _safe(loaded["error"], 300)
        )
    suite = loaded.get("content")
    if suite is None:
        # F21: same two facts as qa_export_suite, said apart.
        return _reply._suite_unreadable_reply(suite_id, loaded)
    total = len(getattr(suite, "test_cases", []) or [])
    result = await _push_suite_cases(suite, req, progress)
    if isinstance(result, str):
        return result
    if result.get("error"):
        return "\u26a0\ufe0f " + _safe(result["error"], 400)
    content = result.get("content") or {}
    await _common._audit(
        "mcp_push_suite",
        entity_id=suite_id,
        detail={
            "target": req.target,
            "apply": bool(req.apply),
            "pushed": int(content.get("pushed") or 0),
            "skipped": int(content.get("skipped") or 0),
            "would_push": int(content.get("would_push") or 0),
        },
    )
    return _push_suite_reply(
        content, req, enabled=enabled, flag_name=flag_name, total=total
    )
