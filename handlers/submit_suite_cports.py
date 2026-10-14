"""Module-backed ports and the ``_ports`` assembler (leaf of ``handlers.submit_suite``)."""

from __future__ import annotations

import types
from typing import Any

from agents.host_submit_ports_prep import SubmitPorts
from handlers.submit_suite_host import host
from handlers.submit_suite_ports import (
    _dup_port,
    _io_port,
    _media_port,
    _out_port,
    _review_port,
)


def _attrs(mod: Any, names: str) -> Any:
    """Namespace over ``mod``: each name reads the public ``name``, else ``_name``."""
    found = {}
    for n in names.split():
        found[n] = getattr(mod, n) if hasattr(mod, n) else getattr(mod, "_" + n)
    return types.SimpleNamespace(**found)


def _common_port() -> Any:
    h = host()
    ns = _attrs(h._common, "audit emit capture_error test_cases_only")
    ns.boot_version = lambda: h._common.BOOT_VERSION
    return ns


def _reply_port() -> Any:
    reply = host()._reply
    ns = _attrs(
        reply,
        "ReplySection GenerationReply shape_generation_result summary_budget "
        "assemble_finalize_reply suite_unreadable_reply oversized_full_suite_note",
    )
    ns.P_REPORT = reply._REPLY_P_REPORT
    ns.P_PROVENANCE = reply._REPLY_P_PROVENANCE
    ns.P_POINTER = reply._REPLY_P_POINTER
    ns.P_EXPORTED = reply._REPLY_P_EXPORTED
    return ns


def _category_port() -> Any:
    return _attrs(
        host().submit_category,
        "staged_resubmit_hint volume_floor_gate step_assertion_gate "
        "step_assertion_gen_notes staged_row_count quality_advisory_gen_notes "
        "oversized_submission_note early_gen_notes checklist_gap_gen_notes "
        "ambiguity_gen_notes",
    )


def _image_port() -> Any:
    return _attrs(
        host().generate_prepare,
        "text_only_image_skip_note attested_image_gap_note",
    )


def _prep_load_port() -> Any:
    return _attrs(
        host().prep_status,
        "version_skew_note code_skew_note load_open_prep superseded_sidecar_note "
        "merge_category_rows prep_store_error_reply fanout_incomplete_note "
        "merge_retained_sidecar",
    )


def _prep_gate_port() -> Any:
    ps = host().prep_status
    ns = _attrs(
        ps,
        "with_retained_sidecar dropped_note rtm_trace_detail "
        "rtm_orphan_note no_coverage_signal_note",
    )
    ns.image_relevance_gate = ps.prep_image_relevance_gate
    ns.volume_floor_note = ps.prep_volume_floor_note
    return ns


def _elicit_port() -> Any:
    el = host()._elicit
    ns = _attrs(el, "unrecognized_client_note union_provenance recent_suites_markdown")
    ns.dispatch_provenance = el._dispatch_provenance  # kept private: foreign patch seam
    return ns


def _ports() -> SubmitPorts:
    """Ports built per call from handler globals, so test patches stay effective."""
    return SubmitPorts(
        io=_io_port(),
        review=_review_port(),
        media=_media_port(),
        image=_image_port(),
        dup=_dup_port(),
        common=_common_port(),
        elicit=_elicit_port(),
        reply=_reply_port(),
        out=_out_port(),
        category=_category_port(),
        prep_load=_prep_load_port(),
        prep_gate=_prep_gate_port(),
    )
