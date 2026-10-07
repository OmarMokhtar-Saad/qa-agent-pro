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

import json
import logging

from tools.mobile import knowledge_hooks, knowledge_limits
from tools.mobile.knowledge_hooks import Directive

logger = logging.getLogger(__name__)

PRIORITY = 20
MAX_DISMISS = 2


def op_rid(step: object) -> tuple[str, str]:
    """``(op, rid)`` of an action model or dict ("" when absent)."""
    body = step if isinstance(step, dict) else getattr(step, "model_dump", lambda **_: {})(mode="json")
    target = body.get("target") if isinstance(body.get("target"), dict) else {}
    return str(body.get("op") or ""), str(target.get("rid") or body.get("rid") or "")


def screen_rids(screen: object) -> set:
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


def _snap_rows(ctx: object, key: str) -> list:
    knowledge = getattr(ctx, "knowledge", None)
    snap = knowledge.get("snap") if isinstance(knowledge, dict) else None
    rows = snap.get(key) if isinstance(snap, dict) else None
    return rows if isinstance(rows, list) else []


def _fired(ctx: object, item: str) -> int:
    return sum(
        1
        for ev in getattr(ctx, "kbuf", None) or []
        if isinstance(ev, dict) and ev.get("kind") == "popup_fired" and ev.get("item") == item
    )


def _dismiss_step(rid: str):
    from tools.mobile import actions

    got = actions.parse_script({"actions": [{"op": "tap", "target": {"rid": rid}}]})
    script = got.get("content")
    return list(getattr(script, "actions", None) or [])


def _match(ctx: object, key: str, step: object, screen: object) -> tuple:
    """``(row, rid)`` of the active popup to dismiss now, else ``(None, "")``."""
    from tools.mobile import actions

    op, want = op_rid(step)
    # Only an op that actuates is blocked by a popup; an assert or a wait is not.
    if op not in actions.MUTATING_OPS or op in actions.WAIT_OPS:
        return None, ""
    here = screen_rids(screen)
    if not want or want in here or want.split("/", 1)[-1] in here:
        return None, ""
    for row in _snap_rows(ctx, "popups"):
        dismiss = row.get("dismiss") if isinstance(row.get("dismiss"), dict) else {}
        rid = str(dismiss.get("rid") or "")
        if dismiss.get("screen_key") == key and rid and (rid in here or rid.split("/", 1)[-1] in here):
            return row, rid
    return None, ""


def pre_step(ctx, entry, step, screen) -> Directive:
    key = str((entry or {}).get("screen_key") or "")
    if not key or not _snap_rows(ctx, "popups"):
        return knowledge_hooks.NONE
    row, rid = _match(ctx, key, step, screen)
    if row is None:
        return knowledge_hooks.NONE
    item = "popups:%s" % row.get("id")
    if _fired(ctx, item) >= MAX_DISMISS:
        return knowledge_hooks.NONE
    from tools.mobile import knowledge_stage_popups

    if knowledge_stage_popups.looks_destructive(rid):
        knowledge_hooks.kbuf_add(ctx, {"kind": "popup_refused", "item": item, "why": "destructive dismiss"})
        return knowledge_hooks.NONE
    steps = _dismiss_step(rid)[: knowledge_limits.PRECONDITION_STEPS]
    if not steps:
        return knowledge_hooks.NONE
    knowledge_hooks.kbuf_add(ctx, {"kind": "popup_fired", "item": item, "rid": rid})
    return Directive(kind="insert_steps", steps=steps, reason="known popup", fired={"item": item})


def post_step(ctx, entry, before, after) -> None:
    """Stamp ``entry["popup"]`` on the dismiss tap a popup directive inserted."""
    _, rid = op_rid((entry or {}).get("action") or {})
    for ev in getattr(ctx, "kbuf", None) or []:
        if ev.get("kind") == "popup_fired" and ev.get("rid") == rid and not ev.get("merged"):
            ev["merged"] = True
            entry["popup"] = str(ev.get("item"))[:80]
            return


def snapshot(conn, facts) -> dict:
    """Active popups only: ``[{id, signature, dismiss}]``."""
    from tools.mobile import knowledge_db

    out = []
    for row in knowledge_db.rows(
        conn, "popups", "status = 'active' AND invalid_at IS NULL", (), knowledge_limits.SNAPSHOT_MAX_ROWS
    ):
        try:
            dismiss = json.loads(row.get("dismiss_json") or "{}")
        except ValueError:
            dismiss = {}
        out.append({"id": row["id"], "signature": row["signature"], "dismiss": dismiss})
    return {"popups": out}


def flush(package: str, run_id: str, events: list) -> None:
    """Record each fired popup once as a counter event (no counter changes)."""
    from tools.mobile import knowledge_db

    fired = [e for e in events or [] if isinstance(e, dict) and e.get("kind") == "popup_fired"]
    if not fired:
        return
    conn = knowledge_db.open_rw(package)
    if conn is None:
        return
    try:
        for item in sorted({str(e.get("item")) for e in fired}):
            parsed = knowledge_db.parse_item_id(item)
            found = []
            if parsed and parsed[0] == "popups":
                found = knowledge_db.rows(conn, "popups", "id = ? AND invalid_at IS NULL", (parsed[1],), 1)
            if found:
                key = {"signature": found[0]["signature"]}
                knowledge_db.upsert_counter(conn, "popups", key, {}, run_id=run_id, why="fired")
    finally:
        conn.close()
