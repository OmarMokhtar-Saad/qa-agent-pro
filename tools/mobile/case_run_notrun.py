"""Case runner: hand the host the actions a stopped script did not run (fix/1460 R2).

``replay.steps_notrun`` names them on the replay result as ``not_run`` /
``not_run_from``, but those indexes count the COMBINED list (the carried queue
first, then the host's own submission). The host only knows its own steps, so the
payload also says how many queued steps came first and where the host's own
numbering resumes.
"""

from __future__ import annotations

from tools.mobile.case_run_evidence import _prior_content


def queued_before(run_id: str, tc_id: str) -> int:
    """How many carried queue steps this submit replayed ahead of the host's own."""
    return len(list(_prior_content(run_id, tc_id).get("queued_actions") or []))


def attach_not_run(payload: dict, result: dict, queued_n: int) -> dict:
    """*payload*'s content, plus the structured ``not_run`` fields of *result*."""
    content, notrun = payload.get("content"), result.get("not_run")
    if not isinstance(content, dict) or not isinstance(notrun, list):
        return payload
    start = int(result.get("not_run_from") or 1)
    content["not_run"] = list(notrun)
    content["not_run_from"] = start
    content["not_run_queued_before"] = queued_n
    content["not_run_from_submit"] = max(1, start - queued_n)
    if "not_run_truncated" in result:
        content["not_run_truncated"] = bool(result["not_run_truncated"])
    return payload
