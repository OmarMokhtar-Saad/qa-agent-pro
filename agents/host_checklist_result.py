"""Host mode: the validated ``checklist_items`` result and its shape caps (split from host_checklist.py)."""

from __future__ import annotations

import dataclasses
import logging
import re

from tools import settings_port

logger = logging.getLogger("agents.host_mode")

# Shape caps on the UNTRUSTED `checklist_items` field. The item cap mirrors
# QA_CHECKLIST_MAX_ITEMS (the server-side decomposition's own cap) so a host
# cannot inflate past what the server path would have produced; the length cap is
# generous for one EARS sentence and finite so one enormous string cannot ride
# into an export, the XLSX sheet or the similarity matrix.
_CL_MAX_TEXT_CHARS = 400
_CL_MIN_TEXT_CHARS = 5
_CL_MAX_NOTES = 10
_CL_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


@dataclasses.dataclass
class HostChecklistResult:
    """Validated result of the host's `checklist_items` field.

    ``ran`` is False when the field was absent or UNUSABLE. In that case ``items``
    is EMPTY and the server does NOT fall back to decomposing the ticket itself --
    the whole point of the flag is that it makes no such call. The suite finalizes
    with no requirements checklist and the reply says so. This is Phase 5d's
    rule written down again: an empty or unexpected host answer counts as EMPTY,
    never as matched.
    """

    ran: bool = False
    requested: bool = False
    items: list = dataclasses.field(default_factory=list)
    notes: list = dataclasses.field(default_factory=list)
    dropped: int = 0
    renumbered: int = 0


def _cl_note(res: HostChecklistResult, msg: str) -> None:
    """Record ``msg`` on ``res`` unless the note cap is already reached."""
    if len(res.notes) < _CL_MAX_NOTES:
        res.notes.append(msg)


def _note_checklist_adjustments(res: HostChecklistResult) -> None:
    """Note the entries dropped and the host ids discarded while building."""
    if res.dropped:
        _cl_note(
            res,
            f"{res.dropped} entr(ies) in `checklist_items` were not usable "
            "requirements and were dropped.",
        )
    if res.renumbered:
        _cl_note(
            res,
            f"{res.renumbered} item(s) carried an id from the host; every id "
            "was DISCARDED and reassigned in order (CL-001 ...). Ids are "
            "server-assigned because the coverage report and the exported "
            "sheet reference them.",
        )


def _bounded_checklist_entries(raw: list, res: HostChecklistResult) -> list:
    """``raw`` cut to QA_CHECKLIST_MAX_ITEMS entries, noting a truncation."""
    try:
        max_items = int(
            getattr(settings_port.get(), "qa_checklist_max_items", 200) or 200
        )
    except (TypeError, ValueError, OverflowError):
        max_items = 200
    max_items = max(1, max_items)
    entries = list(raw)
    if len(entries) > max_items:
        _cl_note(
            res,
            f"`checklist_items` carried {len(entries)} entries -- only the "
            f"first {max_items} were read (QA_CHECKLIST_MAX_ITEMS).",
        )
        entries = entries[:max_items]
    return entries


def _finish_checklist_result(
    res: HostChecklistResult, out: list
) -> HostChecklistResult:
    """Adopt ``out`` into ``res``, or note that nothing usable survived."""
    if not out:
        _cl_note(
            res,
            "`checklist_items` contained no usable requirement, so it is "
            "treated as an UNUSABLE field. Nothing was decomposed to replace "
            "it: this run has no requirements checklist.",
        )
        return res
    res.items = out
    res.ran = True
    logger.info("host checklist accepted: %d requirement(s)", len(out))
    return res
