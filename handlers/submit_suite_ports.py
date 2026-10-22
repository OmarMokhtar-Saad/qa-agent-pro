"""Port namespaces handed to the host-submit pipeline (leaf of ``handlers.submit_suite``).

Every name a test patches on the facade is read through ``host()`` at call time.
"""

from __future__ import annotations

import types
from typing import Any

from handlers.submit_suite_host import host
from tools import settings_port
from tools.suite_store import SuiteMeta


def _attrs(mod: Any, names: str) -> Any:
    """Namespace over ``mod``: each name reads ``_name`` if present, else ``name``."""
    found = {}
    for n in names.split():
        found[n] = getattr(mod, n) if hasattr(mod, n) else getattr(mod, "_" + n)
    return types.SimpleNamespace(**found)


async def _save_suite(suite: Any, **meta: Any) -> dict:
    """Bind the pipeline's plain kwargs into the store's ``SuiteMeta``."""
    return await host().save_suite(suite, meta=SuiteMeta(**meta))


def _io_port() -> Any:
    h = host()
    return types.SimpleNamespace(
        save_suite=_save_suite,
        save_checklist=h.save_checklist,
        load_suite=h.load_suite,
        earlier_suites=h.earlier_suites,
        family_of=h.family_of,
        set_export_path=h.set_export_path,
        finalize_generation=h.finalize_generation,
        deserialize_prepared=h.deserialize_prepared,
        parse_host_suite=h.parse_host_suite,
        code_fingerprint=h.code_fingerprint,
        settings=settings_port.get(),
        try_retain=h.try_retain_sidecar,
        sidecar_merge=h.sidecar_merge,
    )


def _review_port() -> Any:
    h = host()
    return types.SimpleNamespace(
        build_host_ac_section=h.build_host_ac_section,
        extract_host_acs=h.extract_host_acs,
        extract_host_checklist=h.extract_host_checklist,
        build_host_checklist_section=h.build_host_checklist_section,
        build_grounding_section=h.build_grounding_section,
        route_ungrounded_cases=h.route_ungrounded_cases,
        extract_ambiguity_result=h.extract_ambiguity_result,
        build_ambiguity_result_section=h.build_ambiguity_result_section,
    )


def _media_port() -> Any:
    h = host()
    return types.SimpleNamespace(
        extract_host_image_descriptions=h.extract_host_image_descriptions,
        build_host_image_section=h.build_host_image_section,
        image_relevance_counts=h.image_relevance_counts,
        screen_duplicate_groups=h.screen_duplicate_groups,
        dup_shortlist_cases_json=h.dup_shortlist_cases_json,
        apply_duplicate_groups=h.apply_duplicate_groups,
        host_dedup_dup_agreements=h.host_dedup_dup_agreements,
        build_dup_contradiction_headline=h.build_dup_contradiction_headline,
        build_dup_contradiction_pairs=h.build_dup_contradiction_pairs,
        build_dup_shortlist_counted=h.build_dup_shortlist_counted,
    )


def _dup_port() -> Any:
    return types.SimpleNamespace(build_duplicate_section=host().build_duplicate_section)


def _out_port() -> Any:
    h = host()
    return types.SimpleNamespace(
        auto_export_xlsx=h.export.auto_export_xlsx,
        persist_suite_to_corpus=h.generate.gen_persist_suite,
        duplicate_case_note=h.generate.gen_duplicate_case_note,
    )
