"""Host mode: prepared-generation (de)serialization and the Prep*Error contract. Cut verbatim from agents/host_mode.py (P4.3)."""

from __future__ import annotations

import dataclasses
import logging

from tools.atomic_checklist import ChecklistItem
from tools.bilingual import LanguagePair
from tools.rtm import AcceptanceCriterion
from tools.rule_packs import RulePackLine, RulePackResult
from tools.standing_rules import Triggers

logger = logging.getLogger("agents.host_mode")

# Bump this whenever the serialized shape changes incompatibly. A prep record
# persists in SQLite across server restarts AND across auto-updates (users
# auto-update from GitHub Releases), so a record written by an older build can be
# read by a newer one. deserialize_prepared REJECTS any other version rather than
# half-rehydrating a mismatched shape -- the caller (ops-3d) then treats it
# exactly like a stale/unknown prep_id.
_SCHEMA_VERSION = 1

# Every field of PreparedGeneration this serializer knows how to represent. If a
# future field is added to the dataclass and NOT added here, serialize_prepared
# raises PrepSerializeError instead of silently dropping it, and
# tests/test_prep_serialization.py asserts this set equals
# dataclasses.fields(PreparedGeneration) -- two independent guards against a
# silently-forgotten field.
_KNOWN_FIELDS = frozenset(
    {
        "user_msg",
        "rtm_hint",
        "feature_text",
        "complexity_text",
        "acs",
        "source_acs",
        "checklist_items",
        "checklist_presented_ids",
        "checklist_audit",
        "rule_packs",
        "ui_content",
        "parent_context",
        "cache_prefix_warm",
        "jira_image_text",
        "attached_image_text",
        "jira_context_text",
        "image_notice",
        "categories",
        "category_response_schema",
        "target_description",
    }
)

# Plain string fields carried VERBATIM (JSON-native already).
_STR_FIELDS = (
    "user_msg",
    "rtm_hint",
    "feature_text",
    "complexity_text",
    "parent_context",
    "jira_image_text",
    "attached_image_text",
    "jira_context_text",
    "image_notice",
    "target_description",
)

# bool / dict|None fields carried VERBATIM (JSON-native already).
_VERBATIM_FIELDS = (
    "ui_content",
    "checklist_audit",
    "category_response_schema",
    "cache_prefix_warm",
)


class PrepSerdeError(Exception):
    """Base for prep (de)serialization failures. The ops-3d tool wraps it and
    returns a plain tool error -- it never propagates to the MCP client."""


class PrepSerializeError(PrepSerdeError):
    """A PreparedGeneration field could not be represented losslessly. Raised
    LOUDLY rather than dropping fidelity, so the caller returns an error instead
    of persisting a corrupt prep record."""


class PrepDeserializeError(PrepSerdeError):
    """A stored payload is malformed, tampered, or a wrong/unknown schema
    version. The caller treats this exactly like a stale/unknown prep_id."""


class PrepParseError(PrepSerdeError):
    """Host-submitted suite JSON could not be extracted/validated. A sibling of
    the (de)serialize errors so ops-3d catches the whole PrepSerdeError family and
    turns a bad submission into a tester-readable "your JSON did not parse" reply
    rather than a stack trace."""


# --------------------------------------------------------------------------- #
# rule_packs -- the landmine (see plan ITEM 2, the correction section)
# --------------------------------------------------------------------------- #


def _serialize_rule_packs(rp: RulePackResult) -> dict:
    """RulePackResult -> JSON-native dict.

    Three of its fields are non-primitive and must be handled explicitly:

    * ``lines: list[RulePackLine]`` -- a frozen dataclass; ``asdict`` is safe.
    * ``triggers: Triggers`` -- a dataclass with list fields; ``asdict`` is safe
      (its ``fired`` is a @property, not a field, so it is not serialized).
    * ``pairs: list[LanguagePair]`` -- NOT a dataclass. ``LanguagePair``
      (tools/bilingual.py) is deliberately a plain ``__slots__`` class, so
      ``dataclasses.asdict`` does NOT recurse into it -- it deepcopies the object
      straight through, and the break surfaces LATER and less obviously as
      ``TypeError: Object of type LanguagePair is not JSON serializable`` at
      json.dumps time. Each pair is therefore dumped field-by-field here.
    """
    return {
        "lines": [dataclasses.asdict(line) for line in rp.lines],
        "pairs": [
            {"key": p.key, "en": p.en, "ar": p.ar, "source_line": p.source_line}
            for p in rp.pairs
        ],
        "triggers": dataclasses.asdict(rp.triggers),
        "source_ref": rp.source_ref,
        "bilingual_on": rp.bilingual_on,
        "atomicity_on": rp.atomicity_on,
        "standing_on": rp.standing_on,
        "checklist_mode": rp.checklist_mode,
    }


def _deserialize_rule_packs(d: dict) -> RulePackResult:
    """Inverse of ``_serialize_rule_packs``. Rebuilds each nested object from a
    FIXED class -- no payload-driven construction.

    ``LanguagePair.__init__`` re-runs ``normalize_key`` on the key, so the round
    trip is only stable for an already-normalised key. Every key that reaches a
    PreparedGeneration is already normalised (``extract_language_pairs`` and the
    ``RP-I18N-<KEY>`` line ids both go through ``normalize_key``), and
    normalization is idempotent -- the round-trip test asserts this invariant.
    """
    if not isinstance(d, dict):
        raise PrepDeserializeError("rule_packs payload is not an object")
    try:
        lines = [RulePackLine(**ld) for ld in d.get("lines") or []]
        pairs = [
            LanguagePair(
                key=pd["key"],
                en=pd["en"],
                ar=pd["ar"],
                source_line=pd.get("source_line", ""),
            )
            for pd in d.get("pairs") or []
        ]
        triggers = Triggers(**(d.get("triggers") or {}))
        return RulePackResult(
            lines=lines,
            pairs=pairs,
            triggers=triggers,
            source_ref=str(d.get("source_ref", "")),
            bilingual_on=bool(d.get("bilingual_on", False)),
            atomicity_on=bool(d.get("atomicity_on", False)),
            standing_on=bool(d.get("standing_on", False)),
            checklist_mode=bool(d.get("checklist_mode", False)),
        )
    except PrepDeserializeError:
        raise
    except Exception as exc:  # KeyError / TypeError from tampered shapes
        raise PrepDeserializeError(f"invalid rule_packs payload: {exc}") from exc


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def serialize_prepared(prepared) -> dict:
    """PreparedGeneration -> a JSON-serializable dict (``json.dumps``-safe).

    Raises PrepSerializeError if ANY field cannot be represented losslessly --
    never silently drops fidelity.
    """
    # Fail loudly if the dataclass grew a field this serializer does not handle,
    # rather than shipping a prep record that silently loses it.
    actual = {f.name for f in dataclasses.fields(prepared)}
    unknown = actual - _KNOWN_FIELDS
    if unknown:
        raise PrepSerializeError(
            "PreparedGeneration has field(s) the serializer does not handle: "
            f"{sorted(unknown)} -- update agents.host_prep_serde._KNOWN_FIELDS"
        )

    try:
        payload: dict = {"_v": _SCHEMA_VERSION}
        for name in _STR_FIELDS:
            payload[name] = getattr(prepared, name)
        for name in _VERBATIM_FIELDS:
            payload[name] = getattr(prepared, name)

        payload["acs"] = [dataclasses.asdict(a) for a in prepared.acs]
        payload["source_acs"] = [dataclasses.asdict(a) for a in prepared.source_acs]
        payload["checklist_items"] = [
            it.model_dump() for it in prepared.checklist_items
        ]
        # checklist_presented_ids is a list[str] (format_checklist_prompt_block ->
        # tuple[str, list[str]]); coerce any iterable to a list defensively so a
        # set would also round-trip (membership-preserving).
        payload["checklist_presented_ids"] = list(
            prepared.checklist_presented_ids or []
        )
        payload["rule_packs"] = _serialize_rule_packs(prepared.rule_packs)
        # tuples are JSON-lossy (json turns them into lists); store as lists and
        # re-tuple on load, or downstream tuple-unpacking breaks.
        payload["categories"] = [list(t) for t in prepared.categories]
        return payload
    except PrepSerializeError:
        raise
    except Exception as exc:
        raise PrepSerializeError(
            f"could not serialize PreparedGeneration: {exc}"
        ) from exc


def serialize_adopted_state(prepared) -> dict:
    """The SUBMIT-time-adopted subset of a prep, in ``serialize_prepared`` format.

    Residue R4. ``serialize_prepared`` runs exactly once, at PREPARE time. Every
    boomerang whose return field is adopted onto the rehydrated ``prepared``
    object at SUBMIT time therefore lives only in that request's memory -- which
    is fine for a submit that finalizes, and a silent DATA LOSS for the one
    submit that does not: the gap-remediation round writes the envelope back with
    ``prep_store.update_prep`` and returns, so round 2 rehydrates the PREPARE-time
    state again.

    Before R4 that was harmless for the checklist, because the server decomposed
    it at prepare time and it was in the envelope from the start. With
    CHECKLIST_JOB the prepare-time list is EMPTY by construction, so an
    un-carried remediation round would finalize with the checklist items lost --
    an empty Requirements Checklist sheet on a suite that WAS decomposed, the
    exact failure that loop exists to prevent.

    Returns ONLY the fields a submit can adopt, so merging it over the stored
    ``prepared`` dict cannot disturb anything else:

      * ``checklist_items`` / ``checklist_presented_ids`` / ``checklist_audit``
        -- adopted by the CHECKLIST_JOB block (R4).
      * ``acs`` -- adopted by the AC_JOB block, which has the SAME exposure and
        lost the host's derived criteria on every remediation round since Phase
        3a; carried here rather than left as a known silent bug next door.

    Deliberately NOT a full ``serialize_prepared`` re-run. Never raises --
    returns {} on any failure, which degrades to exactly the pre-R4 behaviour.
    """
    try:
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
    except Exception:  # pragma: no cover - defensive; must never break a submit
        logger.debug("serialize_adopted_state failed", exc_info=True)
        return {}


def deserialize_prepared(payload: dict):
    """Inverse of ``serialize_prepared`` -> a REAL PreparedGeneration that
    ``_finalize_generation`` consumes unchanged.

    Rejects (PrepDeserializeError) a non-dict, a missing/unknown schema version,
    or any structurally invalid field. Constructs ONLY fixed known classes -- no
    eval, no __import__, no payload-driven class lookup -- because the payload is
    UNTRUSTED on the way back in (it round-trips through SQLite and ops-3d treats
    host input as untrusted).
    """
    # Imported here, not at module top, so that importing agents.host_prep_serde does
    # not drag in the heavy agent module (and to keep the no-server-import
    # discipline obvious): the ONLY place this batch touches the agent is to
    # reconstruct the dataclass it owns.
    from agents.test_scenario_agent import PreparedGeneration

    _check_prep_envelope(payload)
    try:
        return PreparedGeneration(**_prepared_fields(payload))
    except PrepDeserializeError:
        raise
    except Exception as exc:  # KeyError / TypeError / ValidationError from tampering
        raise PrepDeserializeError(f"malformed prep payload: {exc}") from exc


def _check_prep_envelope(payload) -> None:
    """Reject a non-dict payload or an unsupported schema version."""
    if not isinstance(payload, dict):
        raise PrepDeserializeError("prep payload is not an object")
    if payload.get("_v") != _SCHEMA_VERSION:
        raise PrepDeserializeError(
            f"unsupported prep schema version {payload.get('_v')!r} "
            f"(this build reads v{_SCHEMA_VERSION})"
        )


def _prepared_fields(payload: dict) -> dict:
    """The keyword arguments for ``PreparedGeneration``, built from an
    untrusted payload. May raise KeyError / TypeError on tampering."""
    acs = [AcceptanceCriterion(**a) for a in payload.get("acs") or []]
    source_acs = [AcceptanceCriterion(**a) for a in payload.get("source_acs") or []]
    checklist_items = [
        ChecklistItem(**it) for it in payload.get("checklist_items") or []
    ]
    rule_packs = _deserialize_rule_packs(payload.get("rule_packs") or {})
    categories = [tuple(t) for t in payload.get("categories") or []]
    return dict(
        user_msg=str(payload["user_msg"]),
        rtm_hint=str(payload["rtm_hint"]),
        feature_text=str(payload["feature_text"]),
        complexity_text=str(payload["complexity_text"]),
        acs=acs,
        source_acs=source_acs,
        checklist_items=checklist_items,
        checklist_presented_ids=list(payload.get("checklist_presented_ids") or []),
        checklist_audit=dict(payload.get("checklist_audit") or {}),
        rule_packs=rule_packs,
        ui_content=payload.get("ui_content"),
        parent_context=str(payload["parent_context"]),
        cache_prefix_warm=bool(payload.get("cache_prefix_warm", False)),
        jira_image_text=str(payload["jira_image_text"]),
        attached_image_text=str(payload["attached_image_text"]),
        jira_context_text=str(payload["jira_context_text"]),
        image_notice=str(payload["image_notice"]),
        categories=categories,
        category_response_schema=dict(payload.get("category_response_schema") or {}),
        # .get, not [...]: a prep record written before this field existed
        # must still load rather than failing the whole boomerang.
        target_description=str(payload.get("target_description", "") or ""),
    )
