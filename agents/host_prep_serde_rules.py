"""Host mode: rule_packs (de)serialization for the prep record."""

from __future__ import annotations

import dataclasses

from agents.host_prep_serde_errors import PrepDeserializeError
from tools.bilingual import LanguagePair, PairSpec
from tools.rule_packs import RulePackLine, RulePackResult
from tools.standing_rules import Triggers


# RulePackResult -> JSON-native dict.
#
# Three of its fields are non-primitive and must be handled explicitly:
#
# * ``lines: list[RulePackLine]`` -- a frozen dataclass; ``asdict`` is safe.
# * ``triggers: Triggers`` -- a dataclass with list fields; ``asdict`` is safe
#   (its ``fired`` is a @property, not a field, so it is not serialized).
# * ``pairs: list[LanguagePair]`` -- NOT a dataclass. ``LanguagePair``
#   (tools/bilingual.py) is deliberately a plain ``__slots__`` class, so
#   ``dataclasses.asdict`` does NOT recurse into it -- it deepcopies the object
#   straight through, and the break surfaces LATER and less obviously as
#   ``TypeError: Object of type LanguagePair is not JSON serializable`` at
#   json.dumps time. Each pair is therefore dumped field-by-field here.
def _serialize_rule_packs(rp: RulePackResult) -> dict:
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


def _rp_lines(d: dict) -> list[RulePackLine]:
    return [RulePackLine(**ld) for ld in d.get("lines") or []]


def _rp_pairs(d: dict) -> list[LanguagePair]:
    return [
        LanguagePair(
            PairSpec(
                key=pd["key"],
                en=pd["en"],
                ar=pd["ar"],
                source_line=pd.get("source_line", ""),
            )
        )
        for pd in d.get("pairs") or []
    ]


# Inverse of ``_serialize_rule_packs``. Rebuilds each nested object from a
# FIXED class -- no payload-driven construction.
#
# ``LanguagePair.__init__`` re-runs ``normalize_key`` on the key, so the round
# trip is only stable for an already-normalised key. Every key that reaches a
# PreparedGeneration is already normalised (``extract_language_pairs`` and the
# ``RP-I18N-<KEY>`` line ids both go through ``normalize_key``), and
# normalization is idempotent -- the round-trip test asserts this invariant.
def _deserialize_rule_packs(d: dict) -> RulePackResult:
    if not isinstance(d, dict):
        raise PrepDeserializeError("rule_packs payload is not an object")
    try:
        return RulePackResult(
            lines=_rp_lines(d),
            pairs=_rp_pairs(d),
            triggers=Triggers(**(d.get("triggers") or {})),
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
