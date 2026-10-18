"""Report-safe action text and redaction: the destructive-guard label and the masked dict."""

from __future__ import annotations

import logging

from pydantic import BaseModel

from tools.mobile import act_scrub, perception
from tools.mobile.act_cred import SECRET_MASK, is_credential_action

logger = logging.getLogger("tools.mobile.actions")


# The on-screen label an action is aiming at, for the destructive guard.
#
# ``clear_app_data`` names no on-screen node at all -- it wipes the whole
# app -- so it cannot be judged by an element label the way a tap or a type
# is. It is represented to the SAME lexicon match instead: a fixed phrase
# carrying the destructive-lexicon term ``erase``, so `executor.py`'s guard
# stops it exactly as it stops any other irreversible control, through the
# ordinary ``destructive_hit(label)`` path and no second guard.
def action_text(action: object) -> str:
    """The on-screen label an action is aiming at, for the destructive guard."""
    if str(getattr(action, "op", "") or "") == "clear_app_data":
        return "erase app data"
    target = getattr(action, "target", None)
    parts = []
    if str(getattr(action, "op", "") or "") == "fill":
        # A fill names its field by LABEL and carries no target.
        parts.append(str(getattr(action, "label", "") or ""))
    if target is not None:
        parts.append(str(getattr(target, "text", "") or ""))
        # camelCase ids are judged raw (CheckOut) AND split (signOut -> sign out).
        parts.append(perception.with_camel_split(getattr(target, "rid", "")))
    return " ".join(part for part in parts if part).strip()


def _masked(payload: dict) -> dict:
    """The payload with a secret or credential value replaced by the mask."""
    # # TWO reasons to mask, and the second is the one that matters now that
    # # the report renders a typed literal: the model's own `secret` marker,
    # # and this server's reading of what the field is CALLED. A model that
    # # forgets the marker used to put the value straight through.
    if payload.get("secret") or is_credential_action(payload):
        if "text" in payload:
            payload["text"] = SECRET_MASK
        if "value" in payload:
            payload["value"] = SECRET_MASK
    return payload


# An action as a dict, with any secret value replaced by ``***``.
#
# Defence in depth: ``run_store._write_json`` redacts on the way to disk, but
# the trace is also returned to the CALLER, rendered into the report and
# handed to ``audit_log``, none of which goes through the store.
def redact_action(action: object) -> dict:
    """An action as a dict, with any secret value replaced by ``***``."""
    try:
        if isinstance(action, BaseModel):
            payload = action.model_dump(mode="json")
        elif isinstance(action, dict):
            payload = dict(action)
        else:
            return {"op": str(action)[:40]}
        act_scrub.register_action(payload)
        return _masked(payload)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.actions.redact_action failed")
        return {"op": "unknown"}
