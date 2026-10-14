"""``qa_mobile_knowledge`` import rules: what an imported row may carry."""

from __future__ import annotations

import json
import re
from typing import Any

from tools.mobile import app_knowledge, knowledge_schema
from tools.mobile import knowledge_gate as gate
from tools.mobile import knowledge_stage_popups as popups_stage

_SKIP_IMPORT = set(gate._KEEP_OUT) | {"_table", "recheck_at", "created"}
#: Evidence an imported row must earn again on this install. Every numeric
#: column with a schema default (counters, timings, retention) is dropped so the
#: default applies; measured samples and locally observed element state restart
#: empty. One local run therefore cannot activate or outrank an import.
_EVIDENCE_NUMERIC = frozenset(
    re.findall(
        r"(\w+) (?:INTEGER|REAL) DEFAULT",
        "".join(knowledge_schema._TABLES.values()) + knowledge_schema._COMMON,
    )
)
_EVIDENCE = {
    "samples_json": "[]",
    "bound_field": "",
    "missing_at": "",
    "last_used_run": "",
}
#: The only tables an import may write. The rest are learned on this install
#: alone: a shortcut or route seed activates after one replay, an imported
#: mistake can be credited to tester trust by one local correction, and an
#: imported edge never promotes yet shadows the local one.
_IMPORTABLE = frozenset({"notes", "lessons", "timings", "popups", "elements"})
#: Open imported rows may take at most this share of a table's row cap, so
#: imports can never leave local learning refused as "cap: full".
_IMPORT_SHARE = 0.5
#: table -> {json column: the shape its schema default has (dict or list)}.
_JSON_SHAPES = {
    table: {
        col: dict if default == "{}" else list
        for col, default in re.findall(
            r"(\w+_json) TEXT DEFAULT '(\{\}|\[\])'", cols + knowledge_schema._COMMON
        )
    }
    for table, cols in knowledge_schema._TABLES.items()
}


def _strict_dumps(value: Any) -> str:
    """Strict JSON: NaN/Infinity raise ValueError (a validation error)."""
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _popup_consistent(values: dict, dismiss: Any) -> bool:
    """The dismiss must be a tap on a named rid that the signature agrees with:
    its activity is the signature's prefix and, when the signature carries no
    anchors, the signature's ``dismiss:<rid>`` basis is that very rid."""
    if not isinstance(dismiss, dict) or dismiss.get("op", "tap") != "tap":
        return False
    rid, sig = dismiss.get("rid"), values.get("signature")
    if not isinstance(rid, str) or not rid or not isinstance(sig, str):
        return False
    activity, bar, basis = sig.partition("|")
    if not bar or str(dismiss.get("activity") or "") != activity:
        return False
    if basis.startswith("dismiss:"):
        return sig == popups_stage.signature(activity, [], rid)
    return bool(basis)


def _note_json(values: dict):
    """``(when, then)`` parsed from the row, or ``None`` when malformed."""
    try:
        when = json.loads(values.get("when_json") or "{}")
        then = json.loads(values.get("then_json") or "{}")
    except (TypeError, ValueError):
        return None
    return (when, then) if isinstance(when, dict) and isinstance(then, dict) else None


def _import_note(values: dict) -> bool:
    # One malformed row is skipped, never aborts the whole import.
    parsed = _note_json(values)
    if parsed is None:
        return False
    got = app_knowledge._validate(
        (values.get("text"), values.get("kind"), parsed[0], parsed[1])
    )
    if isinstance(got, str):
        return False
    try:
        values.update(
            text=got[0],
            when_json=_strict_dumps(got[1]),
            then_json=_strict_dumps(got[2]),
        )
    except ValueError:
        return False
    values["scope_key"] = "%s|%s" % (values["kind"], values["when_json"])
    return True
