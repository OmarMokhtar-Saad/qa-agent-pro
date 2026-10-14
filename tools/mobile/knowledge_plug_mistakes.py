"""Executor plug mistakes: field-binding refusal, text-landed stamp, screen anchors.

``pre_step`` reads only the in-memory snapshot (zero DB I/O). ``post_step`` stamps
the typed field on the entry and buffers one ``screen`` event per key per run;
``flush`` writes those as ``screens`` rows. ``snapshot`` loads the active
mistakes and element bindings.
"""

from __future__ import annotations

import json  # noqa: F401
import logging

from tools.mobile import (  # noqa: F401
    app_knowledge,
    knowledge_db,
    knowledge_hooks,
    knowledge_limits,
    screen_key,
)
from tools.mobile import kplg_mist_store as store
from tools.mobile.kplg_calls import Post, Pre
from tools.mobile.kplg_mist_store import snapshot  # noqa: F401
from tools.untrusted import wrap_untrusted

logger = logging.getLogger(__name__)

PRIORITY = 10


def _typed(step: object) -> tuple:
    """``(field, rid)`` of a non-secret ``type`` step naming both, else ``("", "")``."""
    if getattr(step, "op", "") != "type" or getattr(step, "secret", False):
        return "", ""
    field = str(getattr(step, "field", "") or "")
    rid = str(getattr(getattr(step, "target", None), "rid", "") or "")
    return (field, rid) if field and rid else ("", "")


def _key_of(ctx, entry: dict, screen: object) -> str:
    key = str((entry or {}).get("screen_key") or "")
    if key:
        return key
    return screen_key.screen_key(
        screen,
        str(getattr(ctx, "package", "") or ""),
        str(getattr(ctx, "activity", "") or ""),
    ).key


def _bound(ctx, key: str, rid: str) -> dict:
    snap = (getattr(ctx, "knowledge", None) or {}).get("snap") or {}
    got = ((snap.get("bindings") or {}).get(key) or {}).get(rid)
    return got if isinstance(got, dict) else {}


def _refusal(bound: dict, field: str):
    want = str(bound.get("field") or "")
    reason = wrap_untrusted(
        "app_knowledge",
        "This element was learned to take the field '%s'; the step types '%s' into it. "
        "Use the field it is bound to, or pick the right element." % (want, field),
        limit=400,
    )
    fired = {"note_id": bound.get("id"), "kind": "avoid", "outcome": "avoided", "ms": 0}
    return knowledge_hooks.Directive(
        kind="refuse",
        reason=reason,
        refuse_item="elements:%s" % bound.get("id"),
        fired=fired,
    )


def pre_step(ctx, call: Pre):
    """Refuse typing a different field into an element bound to another one."""
    field, rid = _typed(call.step)
    if not field:
        return None
    bound = _bound(ctx, _key_of(ctx, call.entry, call.screen), rid)
    want = str(bound.get("field") or "")
    if not want or want == field:
        return None
    return _refusal(bound, field)


def _seen(ctx, key: str) -> bool:
    return any(
        ev.get("e") == "screen" and ev.get("key") == key
        for ev in (getattr(ctx, "kbuf", None) or [])
    )


def _note_screen(ctx, screen: object) -> None:
    sk = screen_key.screen_key(
        screen,
        str(getattr(ctx, "package", "") or ""),
        str(getattr(ctx, "activity", "") or ""),
    )
    if not sk.key or sk.key.startswith("sk1d:") or _seen(ctx, sk.key):
        return
    anchors = list(sk.anchors)[: knowledge_limits.MAX_ANCHORS]
    knowledge_hooks.kbuf_add(
        ctx, {"e": "screen", "key": sk.key, "activity": sk.activity, "anchors": anchors}
    )


def post_step(ctx, call: Post) -> None:
    """Stamp the typed field (success path) and buffer the screens seen."""
    entry, before, after = call
    action = entry.get("action") if isinstance(entry, dict) else None
    action = action if isinstance(action, dict) else {}
    if (
        action.get("op") == "type"
        and entry.get("outcome") == "ok"
        and action.get("field")
    ):
        entry["text_field"] = str(action["field"])
        entry["landed"] = True
    for screen in (before, after):
        if screen is not None:
            _note_screen(ctx, screen)


def flush(package, run_id, events) -> None:
    """Insert the buffered screens (a duplicate open key is a no-op)."""
    shots = store.screens(events)
    if not shots:
        return
    conn = store.open_store(package)
    if conn is None:
        return
    try:
        store.write_screens(conn, run_id, shots)
    except Exception:
        logger.exception("knowledge mistakes flush failed")
    finally:
        conn.close()
