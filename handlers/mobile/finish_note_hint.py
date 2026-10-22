"""The one-line "save what you learned" hint on a finished mobile run's reply.

The run is over when this shows, so the hint names the call that still saves a note:
``qa_mobile_test`` with ``package`` and ``note`` (``qa_mobile_notes`` only lists or
retires notes). Pure: it reads the case rows already loaded, never the disk.
"""

from __future__ import annotations

_BAD_VERDICTS = frozenset({"fail", "failed", "warning", "warn"})


def worth_a_note(rows: list) -> bool:
    """True when a case failed or warned, or the run recorded any finding."""
    for row in rows:
        if not isinstance(row, dict):
            continue
        verdict = str(row.get("verdict") or row.get("status") or "").lower()
        if verdict in _BAD_VERDICTS or row.get("findings"):
            return True
    return False


def hint(rows: list, package: str = "") -> str:
    """The hint line (with leading blank line), or ``""`` for a clean run."""
    if not worth_a_note(rows):
        return ""
    pkg = str(package or "").strip() or "<package>"
    return (
        "\n\nSave what you found for the next run: call qa_mobile_test with "
        f"package={pkg} and no goal, plus "
        'note={"kind":"fact","text":"Known issue: ..."} '
        "(without a goal no run starts; the reply only asks what to run). "
        "It is shown at the start of later runs; qa_mobile_notes only lists or "
        "retires notes."
    )
