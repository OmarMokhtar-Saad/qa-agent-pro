"""Host mode: seams from the prepare pipeline back into handler modules (C3c).

Agents never import ``handlers``. Every call that reaches a handler module, or
a name tests patch on ``handlers.generate``, goes through one of these frozen
groups of callables. The handler builds them per call, each field a lambda that
resolves its target at call time, so patches applied after construction hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

Hook = Callable[..., Any]


@dataclass(frozen=True)
class CommonHooks:
    audit: Hook
    emit: Hook
    is_url: Hook
    boot_version: Hook


@dataclass(frozen=True)
class CaptureHooks:
    drop_captures: Hook
    retry_hint: Hook
    resolvable: Hook
    carry_forward: Hook
    max_missing_shown: Hook


@dataclass(frozen=True)
class ImageHooks:
    gate_jira_source: Hook
    normalize_source_plan: Hook
    clamped_count: Hook
    unreadable_note: Hook
    grounding_source_note: Hook


@dataclass(frozen=True)
class JiraHooks:
    resolve_content_json: Hook
    issue_url_for_key: Hook


@dataclass(frozen=True)
class StoreHooks:
    load_suite: Hook
    find_recent_suite: Hook
    query_corpus: Hook
    add_to_corpus: Hook
    replace_source_entries: Hook
    corpus_source_key: Hook
    feature_text_cap: Hook


@dataclass(frozen=True)
class JobHooks:
    build_prepare_payload: Hook
    attach_jobs: Hook
    attach_ambiguity_job: Hook
    image_job: Hook
    checklist_job: Hook
    ac_job: Hook
    image_preflight_job: Hook
    image_relevance_job: Hook


@dataclass(frozen=True)
class RunHooks:
    prepare_generation: Hook
    serialize_prepared: Hook
    code_fingerprint: Hook
    stale_snapshot_note: Hook
    recent_suite_warning_note: Hook
    unfinished_note: Hook
    expected_category_names: Hook
    prepared_case_bounds: Hook


@dataclass(frozen=True)
class NoteHooks:
    safe_snapshot_stamp: Hook
    jira_content_chars: Hook
    condensed_payload_note: Hook
    ticket_image_note: Hook
    captured_screens_note: Hook
    snapshot_note: Hook
    reply_notes: Hook
    server_llm_notice: Hook


@dataclass(frozen=True)
class GenerateHooks:
    common: CommonHooks
    capture: CaptureHooks
    image: ImageHooks
    jira: JiraHooks
    store: StoreHooks
    jobs: JobHooks
    run: RunHooks
    notes: NoteHooks
