"""Flow-safe fields of a trace entry (see ``knowledge_trace``).

``target_text`` is the visible label a tap names. ``flow_action`` is the action reduced
to what a saved flow may keep: selectors and field NAMES, never a typed value (a typed
value becomes a ``{field}`` placeholder, a secret step keeps only its field name).
"""

from __future__ import annotations

from tools.mobile import kplg_popup_ref
from tools.mobile.app_flows_consts import ALLOWED_OPS, PLACEHOLDER_RE

_TYPED = ("type", "fill")
_TARGET_KEYS = ("rid", "text", "label", "role")
_PLAIN_KEYS = ("dir", "key", "max_s", "text", "label")


def target_text(action: dict) -> str:
    return kplg_popup_ref.label_of(action)


def _target(action: dict) -> dict:
    raw = action.get("target") if isinstance(action.get("target"), dict) else {}
    return {k: str(raw[k])[:200] for k in _TARGET_KEYS if raw.get(k)}


def _typed(action: dict, out: dict) -> dict:
    field = str(action.get("field") or "")
    if not field:
        return {}
    out["field"] = field[:80]
    if action.get("secret"):
        out["secret"] = True
    elif PLACEHOLDER_RE.fullmatch("{%s}" % field):
        out["text"] = "{%s}" % field
    else:
        return {}
    return out


def flow_action(action: dict) -> dict:
    """The replayable form of *action*, or ``{}`` when it cannot be kept safely."""
    op = str(action.get("op") or "")
    if op not in ALLOWED_OPS:
        return {}
    out: dict = {"op": op}
    if _target(action):
        out["target"] = _target(action)
    if op in _TYPED:
        if op == "fill" and action.get("label"):
            out["label"] = str(action["label"])[:200]
        return _typed(action, out)
    for key in _PLAIN_KEYS:
        if action.get(key) not in (None, ""):
            out[key] = action[key]
    return out
