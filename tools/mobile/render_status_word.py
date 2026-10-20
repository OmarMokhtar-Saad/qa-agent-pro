"""One status word per turn, shared by the step reply and the final summary.

``finalize_explore_cases`` rewrites a turn still ``needs_model`` as ``done``
when the run stops. The step reply said ``needs_model``; the summary then said
``done`` for the same turn, and the host quoted "done" for a timed-out wait.
The close-out keeps the word the step reply used in ``ended_as`` (older runs:
``closed_from``), and every summary surface reads it through
:func:`status_word` (the word) or :func:`status_label` (word plus status).
"""

from __future__ import annotations

ENDED_AS = "ended_as"


def ended_as(row: object) -> str:
    """The status a closed turn had when its step was replied, else ``""``."""
    body = row if isinstance(row, dict) else {}
    return str(body.get(ENDED_AS) or body.get("closed_from") or "")


def status_word(row: object) -> str:
    """The verdict, else the status a closed turn had when its step was replied."""
    body = row if isinstance(row, dict) else {}
    verdict = str(body.get("verdict") or "")
    if verdict:
        return verdict
    status = str(body.get("status") or "")
    return (ended_as(body) or status) if status == "done" else status


def status_label(row: object) -> str:
    """The word, plus ``(status done)`` when a closed turn ended as another word."""
    word = status_word(row)
    body = row if isinstance(row, dict) else {}
    if not body.get("verdict") and ended_as(body) and body.get("status") == "done":
        return word + " (status done)"
    return word
