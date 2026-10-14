"""Hook builders B (jira, store, job, run) and ``build_hooks`` of the host-mode prepare handler.

See ``generate_hooks_a`` for the call-time resolution rule.
"""

from __future__ import annotations

from agents.host_gen_hooks import (
    GenerateHooks,
    JiraHooks,
    JobHooks,
    RunHooks,
    StoreHooks,
)
from handlers import image_gate, jira, prep_status
from handlers.generate_host import host


def _jira_hooks() -> JiraHooks:
    return JiraHooks(
        resolve_content_json=lambda *a, **k: jira._resolve_jira_content_json(*a, **k),
        issue_url_for_key=lambda *a, **k: host().issue_url_for_key(*a, **k),
    )


def _store_hooks() -> StoreHooks:
    return StoreHooks(
        load_suite=lambda *a, **k: host().load_suite(*a, **k),
        find_recent_suite=lambda *a, **k: host().find_recent_suite_by_source(*a, **k),
        query_corpus=lambda *a, **k: host().query_corpus(*a, **k),
        add_to_corpus=lambda *a, **k: host().add_to_corpus(*a, **k),
        replace_source_entries=lambda *a, **k: host().replace_source_entries(*a, **k),
        corpus_source_key=lambda *a, **k: image_gate._corpus_source_key(*a, **k),
        feature_text_cap=lambda: image_gate._FEATURE_TEXT_METADATA_CAP,
    )


def _job_hooks() -> JobHooks:
    return JobHooks(
        build_prepare_payload=lambda *a, **k: host().build_prepare_payload(*a, **k),
        attach_jobs=lambda *a, **k: host().attach_jobs(*a, **k),
        attach_ambiguity_job=lambda *a, **k: host().attach_ambiguity_job(*a, **k),
        image_job=lambda: host().IMAGE_JOB,
        checklist_job=lambda: host().CHECKLIST_JOB,
        ac_job=lambda: host().AC_JOB,
        image_preflight_job=lambda: host().IMAGE_PREFLIGHT_JOB,
        image_relevance_job=lambda: host().IMAGE_RELEVANCE_JOB,
    )


def _run_hooks() -> RunHooks:
    return RunHooks(
        prepare_generation=lambda *a, **k: host().prepare_generation(*a, **k),
        serialize_prepared=lambda *a, **k: host().serialize_prepared(*a, **k),
        code_fingerprint=lambda *a, **k: host().code_fingerprint(*a, **k),
        stale_snapshot_note=lambda *a, **k: host()._stale_snapshot_note(*a, **k),
        recent_suite_warning_note=lambda *a, **k: host().gen_recent_suite_warning_note(
            *a, **k
        ),
        unfinished_note=lambda *a, **k: prep_status.prep_unfinished_note(*a, **k),
        expected_category_names=lambda *a, **k: host().expected_category_names(*a, **k),
        prepared_case_bounds=lambda *a, **k: host().prepared_case_bounds(*a, **k),
    )


def build_hooks() -> GenerateHooks:
    return GenerateHooks(
        common=host()._common_hooks(),
        capture=host()._capture_hooks(),
        image=host()._image_hooks(),
        jira=host()._jira_hooks(),
        store=host()._store_hooks(),
        jobs=host()._job_hooks(),
        run=host()._run_hooks(),
        notes=host()._note_hooks(),
    )
