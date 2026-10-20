"""``{"id": "#3"}`` in a submitted script -> the hashed id of line ``#3``.

The screen list numbers its lines ``#1``, ``#2`` ... and the run memo keeps the
ids behind them (:func:`run_store.shown_ids`). A script may name an element by
that number; it is translated here, once, before the script is parsed, so the
parser and the resolver never learn a new selector. A number the memo does not
hold is left as written: ``#9`` matches no element, which is a stale miss and
never another element.
"""

from __future__ import annotations

import re

from tools.mobile import act_reply, run_store

_REF = re.compile(r"^#(\d{1,3})$")


def _hashed(value: object, ids: tuple) -> object:
    match = _REF.match(value.strip()) if isinstance(value, str) else None
    if match is None or not 1 <= int(match.group(1)) <= len(ids):
        return value
    return ids[int(match.group(1)) - 1] or value


def _walk(node: object, ids: tuple) -> object:
    if isinstance(node, list):
        return [_walk(item, ids) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: _hashed(item, ids) if key == "id" else _walk(item, ids)
        for key, item in node.items()
    }


def map_refs(raw: object, run_id: str) -> object:
    """*raw* with every ``"id": "#n"`` replaced by the id shown as line ``#n``."""
    if "#" not in str(raw):
        return raw
    ids = run_store.shown_ids(str(run_id or ""))
    decoded = act_reply.decode_reply(raw)
    if not ids or not isinstance(decoded.get("actions"), (list, dict)):
        return raw
    return _walk(decoded, ids)


def unknown_ref_note(ref: object, run_id: str) -> str:
    """Why a left-over ``"id": "#n"`` matched nothing; ``""`` for any other target."""
    value = ref.get("id") if isinstance(ref, dict) else getattr(ref, "id", None)
    if not isinstance(value, str) or _REF.match(value.strip()) is None:
        return ""
    shown = len(run_store.shown_ids(str(run_id or "")))
    return (
        value.strip() + " is not on the shown list; " + str(shown) + " lines were "
        "shown. Pick a #n from the screen below."
    )
