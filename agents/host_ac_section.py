"""Host mode: the provenance section for host-derived acceptance criteria (split from host_ac.py)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.rtm import normalize_ac_id

logger = logging.getLogger("agents.host_mode")


def _unknown_ids_note(unknown_ids: list) -> str:
    """The warning bullet naming cited ids that are missing from the derived list."""
    shown = ", ".join(f"`{i}`" for i in unknown_ids[:10])
    more = f" ...and {len(unknown_ids) - 10} more" if len(unknown_ids) > 10 else ""
    return (
        f">   - ⚠️  {len(unknown_ids)} cited requirement id(s) are "
        f"NOT in the list above: {shown}{more}. The usual cause is a "
        # D3 (2026-08-21): this used to name "a PARALLEL FAN-OUT in
        # which each worker derived its own numbering". That cause is
        # impossible on a stock run once the fan-out ask is retired, so
        # it would misdirect the reader of a real divergence. The
        # detector is deterministic and unchanged -- only the cause
        # sentence moves to the shape that can still occur.
        "category generated in a separate context that derived its own "
        "numbering, so identical ids mean different things per "
        "category. Those cases "
        "trace to nothing and are listed as orphans in the matrix below -- "
        "re-check them before trusting any per-requirement claim."
    )


def _unknown_requirement_ids(acs: list, cases) -> list:
    """Requirement ids the cases cite that are not in ``acs``, first-seen order."""
    known = {a.ac_id for a in acs}
    unknown_ids: list = []
    for tc in cases or []:
        rid = normalize_ac_id(getattr(tc, "requirement_id", None) or "")
        if rid and rid not in known and rid not in unknown_ids:
            unknown_ids.append(rid)
    return unknown_ids


def _ac_none_block(notes: list) -> str:
    """The "no acceptance criteria were derived" block, one bullet per note."""
    head = (
        "> \u2139\ufe0f  **No acceptance criteria were derived.** This "
        "ticket carried none, and this server did not synthesize any -- "
        "that derivation runs in your chat, not on this server. Your "
        "submission carried no usable `acceptance_criteria` field, so "
        "the suite below has NO requirements traceability. Nothing was "
        "invented to fill it.\n"
    )
    return head + "".join(f">   - {n}\n" for n in notes) + "\n"


# DIVERGENCE DETECTOR (deterministic, no LLM). The failure mode is not
# "the parent forgot to pass the list on" -- it is a category written in
# a context that never saw the derived list and numbered its own
# AC-001..AC-00N, so the merged suite cites ids that never existed in
# the ONE returned list. D3 (2026-08-21) retired the ask that made this
# the EXPECTED shape (eight blind workers), but not the shape itself: a
# host may still delegate a category, and step 2 of the generation
# instructions is the prose mitigation. This is the detection, and it
# costs one set difference.
def _ac_derived_lines(acs: list, notes: list, cases) -> list:
    """The provenance banner, the derived criteria, the notes and any divergence bullet."""
    unknown_ids = _unknown_requirement_ids(acs, cases)
    lines = [
        f"> \u267b\ufe0f  **{len(acs)} acceptance criteria were DERIVED BY "
        "YOUR CHAT MODEL** (this ticket carried none and this server made no "
        "LLM call for them). They are MODEL-DERIVED scaffolding for the "
        "traceability matrix below -- **not** requirements read from the "
        "ticket, and not approved by anyone. Check them before you rely on "
        "the RTM:"
    ]
    lines += [f">   - {a.ac_id}: {a.description}" for a in acs]
    lines += [f">   - {n}" for n in notes]
    if unknown_ids:
        lines.append(_unknown_ids_note(unknown_ids))
    return lines


# The bounded provenance block for host-derived acceptance criteria.
#
# Prepended AHEAD of the generated summary, like the duplicate and coverage
# sections, so it can never be cut by the summary's character cap. Its ONE job
# is to stop model-invented criteria being read as ticket requirements in the
# RTM printed a few lines below it. Returns "" when the job was never
# requested. Never raises.
def build_host_ac_section(result, cases=None) -> str:
    """The bounded provenance block for host-derived acceptance criteria."""
    with never_raise(logger, "build_host_ac_section failed", exc_info=True):
        if result is None or not getattr(result, "requested", False):
            return ""
        notes = list(getattr(result, "notes", None) or [])
        if not getattr(result, "ran", False):
            return _ac_none_block(notes)
        acs = list(getattr(result, "acs", None) or [])
        return "\n".join(_ac_derived_lines(acs, notes, cases)) + "\n\n"
    return ""
