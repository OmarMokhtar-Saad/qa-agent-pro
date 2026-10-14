"""Host mode: prep-meta keys 20-32 and the host payload of a prepare (C3c).

Moved from handlers/generate.py. Same stamp-not-live rule as host_gen_meta_a:
submit reads these stamps, never the live flags, so an OLD envelope that lacks a
key behaves byte-identically to before. The fan-out flag and the job builders
arrive through hooks so patches on the handler globals still bite.
"""

from __future__ import annotations

import logging

from agents.host_gen_hooks import JobHooks, RunHooks
from agents.host_gen_types import _PrepRun

logger = logging.getLogger("agents.host_mode")


# Prep meta keys 20-32: image/checklist jobs, Jira recency, fan-out, volume.
#
# jira_updated (I2) and jira_content_chars (F9): the ticket's `fields.updated`
# and how much text this revision yielded, so a LATER prepare for the same
# source can tell it was handed an older or thinner snapshot; read with .get,
# so an older envelope is simply "no baseline".
# host_category_*: whether this prep warns on a duplicate qa_submit_category
# and REFUSES a shrinking one (Batch 1/3).
# parallel_fanout: always True since WD; stamped so the finalize gate and
# prep status read the persisted route.
# volume_*: the generation-VOLUME floor. volume_min_cases MUST be stamped
# (prepared.categories carries no counts); volume_categories is stamped
# unconditionally, as the floor is checked on both finalize routes.
def _image_jira_keys(run: _PrepRun) -> dict:
    """Image/checklist job stamps and Jira recency (meta keys 20-25)."""
    return {
        "host_image_relevance": bool(run.img.relevance),
        "host_image_preflight": bool(run.img.preflight),
        "host_image_require_relevant": bool(run.img.require),
        "host_checklist_job": bool(run.checklist_job),
        "jira_updated": run.notes.snapshot_updated,
        "jira_content_chars": run.notes.content_chars,
    }


def meta_stamps(run: _PrepRun, hooks: RunHooks) -> dict:
    """Prep meta keys 20-32: image/checklist jobs, Jira recency, fan-out, volume."""
    return {
        **_image_jira_keys(run),
        "host_category_resubmit_note": True,
        "host_category_shrink_guard": True,
        "parallel_fanout": True,
        "expected_categories": hooks.expected_category_names(run.prepared),
        "volume_floor": True,
        "volume_min_cases": hooks.prepared_case_bounds(run.prepared)[0],
        "volume_categories": hooks.expected_category_names(run.prepared),
    }


def _image_step_job(run: _PrepRun, jobs: JobHooks) -> list:
    """The one image job this prep ships, or none when the image job is off."""
    if not run.img.job:
        return []
    if run.img.preflight:
        return [jobs.image_preflight_job()]
    return [jobs.image_relevance_job() if run.img.relevance else jobs.image_job()]


def _host_jobs(run: _PrepRun, jobs: JobHooks) -> list:
    """The step-zero jobs this prep ships, in attach order."""
    return (
        ([jobs.ac_job()] if run.ac_job else [])
        + _image_step_job(run, jobs)
        + ([jobs.checklist_job()] if run.checklist_job else [])
    )


def build_prepare_payload(run: _PrepRun, prep_id: str, jobs: JobHooks) -> dict:
    """The host payload for this prep, with the ambiguity and step-zero jobs."""
    payload = jobs.build_prepare_payload(run.prepared, prep_id)
    if run.host_amb:
        payload = jobs.attach_ambiguity_job(payload)
    # The GENERAL job mechanism. Also indexes the ambiguity job attached just
    # above (host_mode._LEGACY_JOB_KEYS); a no-op when neither is on.
    return jobs.attach_jobs(payload, _host_jobs(run, jobs))
