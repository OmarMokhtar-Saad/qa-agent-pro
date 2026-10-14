"""Credential gate of the media lane: a step with a credential stores no picture."""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile import actions as actions_mod

logger = logging.getLogger("tools.mobile.media")


def _as_dict(action: object) -> dict:
    """One action as a plain mapping, whatever shape the caller had it in."""
    if isinstance(action, dict):
        return action
    dump = getattr(action, "model_dump", None)
    if callable(dump):
        got = dump()
        return got if isinstance(got, dict) else {}
    return dict(getattr(action, "__dict__", {}) or {})


def _is_credential_op(body: dict) -> bool:
    """True when this action is a value-bearing op that carries a credential."""
    if str(body.get("op") or "") not in actions_mod.VALUE_BEARING_OPS:
        return False
    return bool(body.get("secret") or actions_mod.is_credential_action(body))


# """True when this script carries a credential, on the SAME two markers the
# trace already masks by: the model's own ``secret`` claim and the server's
# own reading of the field name. Asking the question a third way would be a
# third answer.
#
# Two shapes matter here and both are load-bearing.
#
# ``actions.parse_script`` returns a validated ``Script`` MODEL in
# ``content``, not a list, and that is exactly what ``case_runner`` hands to
# ``start_clip``. So the actions are read off ``script.actions`` and each one
# is dumped to a mapping before it is asked anything. Iterating the model
# itself raises, the never-raise fallback below then returns ``True``, and the
# result is the WORST possible failure of this feature: every step in every
# run reports ``credential_withheld``, no clip and no frame is ever stored,
# and nothing anywhere goes red.
#
# The gate is ``actions.VALUE_BEARING_OPS`` -- ``{"type", "ask_tester"}`` --
# not ``"type"`` alone. ``ask_tester`` IS this lane's own credential path: it
# is how a password reaches the device. Gating on ``type`` would photograph
# precisely the screen that must never be photographed, and a PNG cannot be
# masked after the fact.
def withholds(script: object) -> bool:
    """True when this script carries a credential (see the comments above)."""
    try:
        items: Any = getattr(script, "actions", script)
        return any(_is_credential_op(_as_dict(a)) for a in list(items or []))
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mobile.media.withholds failed")
        return True
