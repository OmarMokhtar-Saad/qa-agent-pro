"""Persist stage of qa_submit_suite (moved from handlers, C3a step 3b).

Provenance stamping, the suite save and the checklist/corpus artifacts.

2026-08-30 audit F3: ``save_suite`` never raises -- it returns the standard
``{"error": ..., "content": None}`` -- so a store failure used to fall through
to the in-memory id and the tester got a success-shaped reply for a suite
nothing had persisted. The note below says what was lost and what still
works, and the reply PROTECTS it. "" on every healthy run, so those replies
stay byte-identical. ``assemble_finalize_reply`` is a bare join: each section
owns its own separators.

Provenance is computed BEFORE the save overwrites the prior suite for this
source. The stored provenance is the UNIONED one (the same helper
qa_submit_category uses), not only the finalizing client's.
"""

from __future__ import annotations

import logging
import time
from typing import Any, NamedTuple

from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubPrep, SubProv, SubSaved

logger = logging.getLogger("agents.host_mode")


class SaveIn(NamedTuple):
    prep: SubPrep
    rows: Any
    suite: Any
    progress: Any


class ProvIn(NamedTuple):
    suite: Any
    rows: Any
    source_url: Any


class ArtifactsIn(NamedTuple):
    prep: SubPrep
    saved: SubSaved
    suite: Any


def persist_failure_note(saved: dict, suite_id: str) -> str:
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


async def stamp_provenance(args: ProvIn, ports: SubmitPorts) -> SubProv:
    suite = args.suite
    cases = list(getattr(suite, "test_cases", None) or [])
    _dup_note, _dup_counts = await ports.out.duplicate_case_note(cases, args.source_url)
    ports.elicit.dispatch_provenance()
    _provenance = ports.elicit.union_provenance(args.rows)
    suite._duplicate_case_note = _dup_note
    suite._duplicate_case_counts = _dup_counts
    suite._provenance = _provenance
    note = ports.elicit.unrecognized_client_note(_provenance, context="suite")
    return SubProv(_dup_note, _dup_counts, _provenance, note)


async def save_submitted_suite(args: SaveIn, ports: SubmitPorts) -> SubSaved:
    prep, suite = args.prep, args.suite
    _t0 = time.monotonic()
    await ports.common.emit(args.progress, "\U0001f4be Saving the suite…")
    _t_emit = time.monotonic()
    prov = await stamp_provenance(ProvIn(suite, args.rows, prep.source_url), ports)
    saved = await ports.io.save_suite(
        suite,
        feature_text=prep.source_text,
        source_url=prep.source_url,
        prep_id=prep.prep_id,
        provenance=prov.provenance,
    )
    logger.info(
        "submit tail: progress emit %.1fs | save_suite %.1fs",
        _t_emit - _t0,
        time.monotonic() - _t_emit,
    )
    suite_id = (saved.get("content") or {}).get("suite_id", "") or suite.suite_id
    return SubSaved(suite_id, persist_failure_note(saved, suite_id), prov)


async def persist_artifacts(args: ArtifactsIn, ports: SubmitPorts) -> None:
    suite, prep = args.suite, args.prep
    _checklist_artifacts = getattr(suite, "_checklist_artifacts", None)
    if _checklist_artifacts and args.saved.suite_id:
        await ports.io.save_checklist(args.saved.suite_id, _checklist_artifacts)
    _t_corpus = time.monotonic()
    await ports.out.persist_suite_to_corpus(
        suite, feature_text=prep.source_text, source_url=(prep.source_url or "")
    )
    logger.info("submit tail: corpus persist %.1fs", time.monotonic() - _t_corpus)
