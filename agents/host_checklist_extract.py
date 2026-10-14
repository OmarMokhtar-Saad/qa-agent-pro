"""Host mode: validate the untrusted ``checklist_items`` field (split from host_checklist.py)."""

from __future__ import annotations

import logging

from agents.host_checklist_items import _build_checklist_items
from agents.host_checklist_result import (
    HostChecklistResult,
    _bounded_checklist_entries,
    _cl_note,
    _finish_checklist_result,
    _note_checklist_adjustments,
)
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")

_NOT_LIST_NOTE = (
    "`checklist_items` was not a list -- the whole field was ignored. "
    "No requirements were decomposed and none were invented."
)


# Validate the SHAPE of the UNTRUSTED top-level `checklist_items` field.
#
# NEVER raises and NEVER trusts the field. Rules, enforced here in Python over
# already-``json.loads``'d data (no eval, no ast, no dynamic attribute access),
# and deliberately the SAME post-processing the server-side decomposition
# already applies (tools/atomic_checklist.decompose_to_checklist) plus the
# hardening a server-authored list never needed:
#
#   * absent / None                -> ran=False, no notes (the common case)
#   * not a list                   -> ran=False + note
#   * a string entry               -> tolerated as its text
#   * a dict entry                 -> `text` (or `item` / `description`),
#                                     optional `ears_pattern`, optional `source`
#   * any other entry type         -> dropped + counted
#   * text under 5 chars           -> dropped (mirrors decompose_to_checklist)
#   * a duplicate normalised text  -> collapsed silently
#   * beyond QA_CHECKLIST_MAX_ITEMS-> truncated + noted
#   * an unknown ears_pattern      -> folded to "ubiquitous" (never rejected)
#   * a source outside the shape
#     allowlist                    -> folded to "unattributed" by
#                                     normalize_source, which also drags the
#                                     granularity audit's provenance ratio down
#   * ANY host-supplied item_id    -> DISCARDED and counted in ``renumbered``.
#     Ids are assigned here, positionally, CL-001 .. CL-NNN, because they are
#     referenced by the exported sheet: a colliding or invented id would
#     silently re-point a requirement row.
#   * ZERO surviving items         -> ran=False + note
def extract_host_checklist(raw, *, requested: bool = True) -> HostChecklistResult:
    """Validate the SHAPE of the UNTRUSTED top-level `checklist_items` field."""
    res = HostChecklistResult(requested=bool(requested))
    # pragma: no cover - defensive; must never break a submit
    with never_raise(logger, "extract_host_checklist failed", exc_info=True):
        if raw is None:
            return res
        if not isinstance(raw, list):
            _cl_note(res, _NOT_LIST_NOTE)
            return res
        entries = _bounded_checklist_entries(raw, res)
        out = _build_checklist_items(entries, res)
        _note_checklist_adjustments(res)
        return _finish_checklist_result(res, out)
    return HostChecklistResult(requested=bool(requested))
