"""Executor plug popups: dismiss a known interrupting screen before the step.

``pre_step`` (PRIORITY 20): when the live screen key matches an ACTIVE popup and
the pending step's target is not on this screen (the step expects a different
screen), insert one dismiss tap, recorded as fired. A dismiss that looks
destructive is refused (nothing inserted). At most ``MAX_DISMISS`` per popup per
run, so a popup that keeps coming back cannot loop. The hot path reads only the
in-memory snapshot (``ctx.knowledge["snap"]["popups"]``); zero DB I/O. A
candidate is never applied. Seams never raise.
"""

from __future__ import annotations

import json  # noqa: F401
import logging
from typing import Any, cast

from tools.mobile import (
    actions,
    knowledge_hooks,
    knowledge_limits,
    knowledge_stage_popups,
    kplg_popup_ref,
)
from tools.mobile.knowledge_hooks import Directive
from tools.mobile.kplg_calls import Post, Pre
from tools.mobile.kplg_popup_db import flush, snapshot  # noqa: F401

logger = logging.getLogger(__name__)

PRIORITY = 20
MAX_DISMISS = 2


def op_rid(step: Any) -> tuple[str, str]:
    """``(op, rid)`` of an action model or dict ("" when absent)."""
    body = (
        step
        if isinstance(step, dict)
        else getattr(step, "model_dump", lambda **_: {})(mode="json")
    )
    target = cast(
        "dict", body.get("target") if isinstance(body.get("target"), dict) else {}
    )
    return str(body.get("op") or ""), str(target.get("rid") or body.get("rid") or "")


def screen_rids(screen: Any) -> set:
    """Resource ids on *screen*, with and without the package prefix."""
    out: set = set()
    elements = screen.get("elements") if isinstance(screen, dict) else None
    for element in elements if isinstance(elements, list) else []:
        rid = str(element.get("rid") or "") if isinstance(element, dict) else ""
        if rid:
            out.add(rid)
            out.add(rid.split(":", 1)[-1])
            out.add(rid.split("/", 1)[-1])
    return out


def _snap_rows(ctx: Any, key: str) -> list:
    knowledge = getattr(ctx, "knowledge", None)
    snap = knowledge.get("snap") if isinstance(knowledge, dict) else None
    rows = snap.get(key) if isinstance(snap, dict) else None
    return rows if isinstance(rows, list) else []


def _fired(ctx: Any, item: str) -> int:
    return sum(
        1
        for ev in getattr(ctx, "kbuf", None) or []
        if isinstance(ev, dict)
        and ev.get("kind") == "popup_fired"
        and ev.get("item") == item
    )


def _dismiss_step(rid: str):

    got = actions.parse_script(
        {"actions": [{"op": "tap", "target": kplg_popup_ref.target_of(rid)}]}
    )
    script = got.get("content")
    return list(getattr(script, "actions", None) or [])


def _blocked(call: Pre) -> set:
    """Resource ids on screen when a popup can block the step, else empty."""

    op, want = op_rid(call.step)
    want = want or kplg_popup_ref.step_ref(call.step)
    # Only an op that actuates is blocked by a popup; an assert or a wait is not.
    if op not in actions.MUTATING_OPS or op in actions.WAIT_OPS:
        return set()
    here = screen_rids(call.screen) | kplg_popup_ref.screen_refs(call.screen)
    if not want or want in here or want.split("/", 1)[-1] in here:
        return set()
    return here


def _dismiss_hit(row: dict, key: str, here: set) -> str:
    dismiss: Any = row.get("dismiss")
    dismiss = dismiss if isinstance(dismiss, dict) else {}
    rid = str(dismiss.get("rid") or "")
    if (
        dismiss.get("screen_key") == key
        and rid
        and (rid in here or rid.split("/", 1)[-1] in here)
    ):
        return rid
    return ""


def _match(ctx: Any, key: str, call: Pre) -> tuple:
    """``(row, rid)`` of the active popup to dismiss now, else ``(None, "")``."""
    here = _blocked(call)
    for row in _snap_rows(ctx, "popups"):
        rid = _dismiss_hit(row, key, here)
        if rid:
            return row, rid
    return None, ""


def _refuse(ctx: Any, item: str) -> Directive:
    knowledge_hooks.kbuf_add(
        ctx, {"kind": "popup_refused", "item": item, "why": "destructive dismiss"}
    )
    return knowledge_hooks.NONE


def _insert(ctx: Any, item: str, rid: str) -> Directive:
    steps = _dismiss_step(rid)[: knowledge_limits.PRECONDITION_STEPS]
    if not steps:
        return knowledge_hooks.NONE
    knowledge_hooks.kbuf_add(ctx, {"kind": "popup_fired", "item": item, "rid": rid})
    return Directive(
        kind="insert_steps", steps=steps, reason="known popup", fired={"item": item}
    )


def _fire(ctx: Any, item: str, rid: str) -> Directive:

    if knowledge_stage_popups.looks_destructive(rid):
        return _refuse(ctx, item)
    return _insert(ctx, item, rid)


def pre_step(ctx, call: Pre) -> Directive:
    key = str((call.entry or {}).get("screen_key") or "")
    if not key or not _snap_rows(ctx, "popups"):
        return knowledge_hooks.NONE
    row, rid = _match(ctx, key, call)
    if row is None:
        return knowledge_hooks.NONE
    item = "popups:%s" % row.get("id")
    if _fired(ctx, item) >= MAX_DISMISS:
        return knowledge_hooks.NONE
    return _fire(ctx, item, rid)


def post_step(ctx, call: Post) -> None:
    """Stamp ``entry["popup"]`` on the dismiss tap a popup directive inserted."""
    entry = call.entry
    action = (entry or {}).get("action") or {}
    rid = op_rid(action)[1] or kplg_popup_ref.step_ref(action)
    for ev in getattr(ctx, "kbuf", None) or []:
        if (
            ev.get("kind") == "popup_fired"
            and ev.get("rid") == rid
            and not ev.get("merged")
        ):
            ev["merged"] = True
            entry["popup"] = str(ev.get("item"))[:80]
            return
