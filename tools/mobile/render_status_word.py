"""One status word per turn, shared by the step reply and the final summary.

``finalize_explore_cases`` rewrites a turn still ``needs_model`` as ``done``
when the run stops. The step reply said ``needs_model``; the summary then said
``done`` for the same turn, and the host quoted "done" for a timed-out wait.
The close-out keeps the word the step reply used in ``closed_from``, and every
summary surface reads it through :func:`status_word`.
"""

from __future__ import annotations


def status_word(row: object) -> str:
    """The verdict, else the status a closed turn had when its step was replied."""
    body = row if isinstance(row, dict) else {}
    verdict = str(body.get("verdict") or "")
    if verdict:
        return verdict
    status = str(body.get("status") or "")
    return str(body.get("closed_from") or status) if status == "done" else status
