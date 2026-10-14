"""Host mode: PreparedGeneration -> JSON-native dict (serialize side of the prep record)."""

from __future__ import annotations

import dataclasses
import logging

from agents.host_prep_serde_consts import (
    _KNOWN_FIELDS,
    _SCHEMA_VERSION,
    _STR_FIELDS,
    _VERBATIM_FIELDS,
)
from agents.host_prep_serde_errors import PrepSerializeError
from agents.host_prep_serde_rules import _serialize_rule_packs
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


# Fail loudly if the dataclass grew a field this serializer does not handle,
# rather than shipping a prep record that silently loses it.
def _check_known_fields(prepared) -> None:
    actual = {f.name for f in dataclasses.fields(prepared)}
    unknown = actual - _KNOWN_FIELDS
    if unknown:
        raise PrepSerializeError(
            "PreparedGeneration has field(s) the serializer does not handle: "
            f"{sorted(unknown)} -- update agents.host_prep_serde._KNOWN_FIELDS"
        )


# checklist_presented_ids is a list[str] (format_checklist_prompt_block ->
# tuple[str, list[str]]); coerce any iterable to a list defensively so a
# set would also round-trip (membership-preserving).
# tuples are JSON-lossy (json turns them into lists); store as lists and
# re-tuple on load, or downstream tuple-unpacking breaks.
def _payload(prepared) -> dict:
    payload: dict = {"_v": _SCHEMA_VERSION}
    for name in _STR_FIELDS:
        payload[name] = getattr(prepared, name)
    for name in _VERBATIM_FIELDS:
        payload[name] = getattr(prepared, name)
    payload["acs"] = [dataclasses.asdict(a) for a in prepared.acs]
    payload["source_acs"] = [dataclasses.asdict(a) for a in prepared.source_acs]
    payload["checklist_items"] = [it.model_dump() for it in prepared.checklist_items]
    payload["checklist_presented_ids"] = list(prepared.checklist_presented_ids or [])
    payload["rule_packs"] = _serialize_rule_packs(prepared.rule_packs)
    payload["categories"] = [list(t) for t in prepared.categories]
    return payload


# PreparedGeneration -> a JSON-serializable dict (``json.dumps``-safe).
#
# Raises PrepSerializeError if ANY field cannot be represented losslessly --
# never silently drops fidelity.
def serialize_prepared(prepared) -> dict:
    _check_known_fields(prepared)
    with never_raise() as g:
        return _payload(prepared)
    if isinstance(g.exc, PrepSerializeError):
        raise g.exc
    raise PrepSerializeError(
        f"could not serialize PreparedGeneration: {g.exc}"
    ) from g.exc


# Residue R4. ``serialize_prepared`` runs exactly once, at PREPARE time. Every
# boomerang whose return field is adopted onto the rehydrated ``prepared``
# object at SUBMIT time therefore lives only in that request's memory -- which
# is fine for a submit that finalizes, and a silent DATA LOSS for the one
# submit that does not: the gap-remediation round writes the envelope back with
# ``prep_store.update_prep`` and returns, so round 2 rehydrates the PREPARE-time
# state again.
#
# Before R4 that was harmless for the checklist, because the server decomposed
# it at prepare time and it was in the envelope from the start. With
# CHECKLIST_JOB the prepare-time list is EMPTY by construction, so an
# un-carried remediation round would finalize with the checklist items lost --
# an empty Requirements Checklist sheet on a suite that WAS decomposed, the
# exact failure that loop exists to prevent.
#
# Returns ONLY the fields a submit can adopt, so merging it over the stored
# ``prepared`` dict cannot disturb anything else:
#
#   * ``checklist_items`` / ``checklist_presented_ids`` / ``checklist_audit``
#     -- adopted by the CHECKLIST_JOB block (R4).
#   * ``acs`` -- adopted by the AC_JOB block, which has the SAME exposure and
#     lost the host's derived criteria on every remediation round since Phase
#     3a; carried here rather than left as a known silent bug next door.
#
# Deliberately NOT a full ``serialize_prepared`` re-run. Never raises --
# returns {} on any failure, which degrades to exactly the pre-R4 behaviour.
def serialize_adopted_state(prepared) -> dict:
    """The SUBMIT-time-adopted subset of a prep, in ``serialize_prepared`` format."""
    # pragma: no cover - defensive; must never break a submit
    with never_raise(logger, "serialize_adopted_state failed", exc_info=True):
        out: dict = {}
        out["checklist_items"] = [
            it.model_dump() for it in getattr(prepared, "checklist_items", None) or []
        ]
        out["checklist_presented_ids"] = [
            str(i) for i in getattr(prepared, "checklist_presented_ids", None) or []
        ]
        out["checklist_audit"] = dict(getattr(prepared, "checklist_audit", None) or {})
        out["acs"] = [
            dataclasses.asdict(a) for a in getattr(prepared, "acs", None) or []
        ]
        return out
    return {}
