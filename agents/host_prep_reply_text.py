"""Reply text shared by every prep tool's refusals and by ``qa_get_category_job``.

Pure text assembly: the handler (handlers/prep_status.py) decides WHEN each reply
applies; this module only builds the words. Host-supplied ids and names are
clipped with ``clip_echo`` before they are echoed.
"""

from __future__ import annotations

import json
from typing import Any

from agents.host_echo import clip_echo


def store_error_text(prep_id: str, err: str) -> str:
    """F6 (2026-09-02 audit): a store READ failure is RETRYABLE, so it must not
    be reported as the missing-prep reply that tells the host to abandon work
    still on disk."""
    return (
        "⚠️ Could not READ the preparation store for prep_id "
        f"`{clip_echo(prep_id, 80)}` -- this is a STORAGE failure, not an "
        "expired prep: " + clip_echo(err, 300) + "\n\n"
        "**Your staged work has NOT been lost and nothing needs regenerating "
        "yet.** Retry the same call once. If it fails again, run `qa-doctor` -- "
        "it reports where the store lives and whether this server can read it."
    )


# 2026-08-03 (Fix 5a): the missing-prep advice used to be a bare "Start again with
# `qa_prepare_test_cases`". A FINISHED suite is the most common way to reach it,
# so that told a host whose suite had just been exported to launch a SECOND full
# generation. Re-preparing is only correct when the work was genuinely LOST, so
# the reply separates the two cases and makes the finished case a stop. It
# deliberately does not distinguish unknown / expired / finalized.
def _missing_head(prep_id: str) -> str:
    return (
        f"⚠️ No active preparation for prep_id "
        f"`{clip_echo(prep_id, 80)}`. It is unknown, "
        "expired (its TTL elapsed), or was already finalized (a finished suite "
        "deletes its prep).\n\n"
        "**Do NOT resubmit this prep_id, and do not re-prepare reflexively.** "
        "Check first:\n"
    )


def _missing_checks() -> str:
    return (
        "- **Did this run already finish?** If a suite summary and an export path "
        "came back for it, the work is DONE and saved -- stop here. Re-preparing "
        "would generate the whole suite a second time and cost every category "
        "again. Use `qa_export_suite` with that suite_id if you need another "
        "format.\n"
        "- **Only if the work was genuinely lost** (no suite was ever finalized, "
        "or the id is expired/unknown) start over with `qa_prepare_test_cases`."
    )


def missing_text(prep_id: str) -> str:
    """One message covering unknown / TTL-expired / already-finalized prep_ids."""
    return _missing_head(prep_id) + _missing_checks()


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


def _finalized_closing(suite_id: str) -> str:
    return (
        "\n**Do NOT resubmit and do NOT re-prepare** -- re-preparing would "
        "generate the whole suite a second time and cost every category again. "
        + (
            f"For another format use `qa_export_suite` with suite_id `{suite_id}`."
            if suite_id
            else "Use `qa_export_suite` if you need another format."
        )
    )


def finalized_text(prep_id: str, suite_id: str, export_path: str) -> str:
    """Reply for a prep whose suite ALREADY finalized successfully (Fix 5b,
    2026-08-03): naming the finished suite and its export path makes "stop"
    actionable instead of advice the host has to trust."""
    lines = _finalized_notice_lines(prep_id)
    if suite_id:
        lines.append(f"- **suite_id:** `{suite_id}`")
    if export_path:
        lines.append(f"- **exported to:** `{export_path}`")
    lines.append(_finalized_closing(suite_id))
    return "\n".join(lines)


def host_task_text(prep_id: str) -> str:
    """Reply naming the id KIND when a loaded prep record is a host_llm task."""
    return (
        f"⚠️ `{prep_id}` is a host-task id (opened by `tools/host_llm.py`), not a "
        "test-case prep id — nothing is corrupted. Submit it with the submit tool "
        "named in that task's envelope, or run `qa_prepare_test_cases` to start a "
        "test-case flow."
    )


def with_step_zero(batch: dict, envelope: Any) -> dict:
    """F3 (2026-08-30): serve the step-zero jobs in the one call the instructions
    tell a host to make. FIRST key in the dict on purpose: a host reading the JSON
    top-down meets the blocking preflight before the categories. An envelope
    written before this key existed carries none, and that reply is unchanged."""
    sz = envelope.get("step_zero") if isinstance(envelope, dict) else None
    if isinstance(sz, dict) and sz.get("jobs_to_run"):
        return {"step_zero": sz, **batch}
    return batch


def _all_jobs_intro(prep_id: str, n: int) -> str:
    return (
        f"## Category jobs — ALL {n} categories (one fetch)\n\n"
        f"`prep_id`: `{prep_id}`\n\n"
        "`shared` applies to EVERY job; `shared` plus one `jobs[]` "
        "entry is the same packet the single-category form returns. "
    )


# D3 (2026-08-21): the retired "one same-session sub-context per jobs[] entry"
# ask is NOT quoted back: a tombstone comment is how an absence grep gets armed
# by the very change that satisfied it.
def _all_jobs_steps() -> str:
    return (
        "**Run every `step_zero` job FIRST, before you generate a single "
        "case.** The ambiguity preflight is `blocking: true`, and the "
        "acceptance-criteria job produces the ac_ids each case's "
        "`requirement_id` has to cite — a case written before those "
        "ids exist can only be tagged null, and the only way to fix that "
        "afterwards is to regenerate the suite. Then generate each "
        "`jobs[]` entry and call `qa_submit_category` for it as soon as "
        "its cases are written — do not fetch categories one call at "
        "a time.\n\n"
    )


def all_jobs_text(batch: dict, prep_id: str) -> str:
    """The "all" form: every job packet in one fenced JSON, step zero first."""
    return (
        _all_jobs_intro(prep_id, len(batch["jobs"]))
        + _all_jobs_steps()
        + "```json\n"
        + json.dumps(batch, ensure_ascii=False, indent=2)
        + "\n```\n"
    )


def no_jobs_text(prep_id: str) -> str:
    return f"⚠️ No category jobs available for prep_id `{prep_id}`."


def unknown_category_text(prep_id: str, name: str) -> str:
    return (
        f"⚠️ Unknown category `{clip_echo(name, 80)}` "
        f"for prep_id `{clip_echo(prep_id, 80)}`. "
        "Use a name from orchestration.expected_categories / categories[].name."
    )


def single_job_text(title: object, prep_id: str, job: dict) -> str:
    """The one-category form: that job packet as fenced JSON. ``title`` is the
    packet's canonical category name (server vocabulary, not the host's echo)."""
    return (
        f"## Category job — **{title}**\n\n"
        f"`prep_id`: `{prep_id}`\n\n"
        "```json\n" + json.dumps(job, ensure_ascii=False, indent=2) + "\n```\n"
    )
