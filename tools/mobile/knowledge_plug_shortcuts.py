"""Executor plug shortcuts: replay a learned multi-step sequence in one go.

``pre_step`` (PRIORITY 30): an ACTIVE shortcut whose ``start_key`` equals the
live screen key, whose precondition rids are on screen and whose first steps
match the model's upcoming steps (op + target rid) is inserted as one
``insert_steps`` directive. It needs the model's upcoming steps
(``ctx.script_ahead``, current step first); without them nothing fires. A
precondition or sequence mismatch skips it, counts a fail in ``ctx.kbuf``
(``flush`` persists it; ``STALE_AFTER_FAILS`` fails make it stale) and the model
carries on silently. Never fires while ``ctx.route_expect`` is set, so a saved
route's own expectation check wins. Each shortcut fires at most once per run.
Hot path: zero DB I/O. Seams never raise.
"""

from __future__ import annotations

import json
import logging

from tools.mobile import knowledge_db, knowledge_hooks, knowledge_limits
from tools.mobile.knowledge_hooks import Directive
from tools.mobile.knowledge_plug_popups import op_rid, screen_rids

logger = logging.getLogger(__name__)

PRIORITY = 30


def _rows(ctx: object) -> list:
    knowledge = getattr(ctx, "knowledge", None)
    snap = knowledge.get("snap") if isinstance(knowledge, dict) else None
    rows = snap.get("shortcuts") if isinstance(snap, dict) else None
    return rows if isinstance(rows, list) else []


def _count(ctx: object, kind: str, item: str) -> int:
    return sum(
        1
        for ev in getattr(ctx, "kbuf", None) or []
        if isinstance(ev, dict) and ev.get("kind") == kind and ev.get("item") == item
    )


def _body(step: object) -> dict:
    if isinstance(step, dict):
        return step
    dump = getattr(step, "model_dump", None)
    return dump(mode="json") if dump else {}


def _build(steps: list, ahead: list) -> list | None:
    """Action dicts for the shortcut steps; a ``type`` step takes its text, secret
    flag and field from the model's matching step. ``None`` when one cannot be filled."""
    out: list = []
    for i, st in enumerate(steps):
        action = {"op": st["op"], "target": {"rid": st["rid"]}}
        if st["op"] == "type":
            if i >= len(ahead):
                return None
            mine = _body(ahead[i])
            for key in ("text", "secret", "field"):
                if mine.get(key) not in (None, ""):
                    action[key] = mine[key]
        out.append(action)
    return out


def _fail(ctx: object, item: str, why: str) -> None:
    knowledge_hooks.kbuf_add(ctx, {"kind": "shortcut_fail", "item": item, "why": why})


def _try(ctx: object, row: dict, screen: object, ahead: list) -> Directive:
    item = "shortcuts:%s" % row.get("id")
    steps = row.get("steps") or []
    if not steps or _count(ctx, "shortcut_fired", item) or _count(ctx, "shortcut_fail", item):
        return knowledge_hooks.NONE
    if len(ahead) < len(steps):
        # Fewer scripted steps left than the shortcut stands in for: it would
        # run steps the script never asked for.
        return knowledge_hooks.NONE
    if op_rid(ahead[0]) != (steps[0].get("op"), steps[0].get("rid")):
        return knowledge_hooks.NONE
    here = screen_rids(screen)
    need = [str(r) for r in (row.get("precondition") or {}).get("rids") or []]
    if any(r not in here and r.split("/", 1)[-1] not in here for r in need):
        _fail(ctx, item, "precondition")
        return knowledge_hooks.NONE
    for i in range(1, len(steps)):
        if op_rid(ahead[i]) != (steps[i].get("op"), steps[i].get("rid")):
            _fail(ctx, item, "sequence")
            return knowledge_hooks.NONE
    built = _build(steps, ahead)
    if not built or len(built) > knowledge_limits.PRECONDITION_STEPS * 4:
        return knowledge_hooks.NONE
    from tools.mobile import actions

    script = actions.parse_script({"actions": built}).get("content")
    parsed = list(getattr(script, "actions", None) or [])
    if len(parsed) != len(built):
        return knowledge_hooks.NONE
    expect = [list(op_rid(p)) for p in parsed]
    knowledge_hooks.kbuf_add(
        ctx, {"kind": "shortcut_fired", "item": item, "left": len(parsed), "expect": expect}
    )
    return Directive(
        kind="insert_steps",
        steps=parsed,
        reason="shortcut",
        fired={"item": item, "kind": "shortcut", "consume": len(steps)},
    )


def pre_step(ctx, entry, step, screen) -> Directive:
    if getattr(ctx, "route_expect", None):
        return knowledge_hooks.NONE
    key = str((entry or {}).get("screen_key") or "")
    ahead = list(getattr(ctx, "script_ahead", None) or [])
    if not key or not ahead or not _rows(ctx):
        return knowledge_hooks.NONE
    for row in _rows(ctx):
        if row.get("start_key") == key:
            got = _try(ctx, row, screen, ahead)
            if got.kind != "none":
                return got
    return knowledge_hooks.NONE


def post_step(ctx, entry, before, after) -> None:
    """Count down the steps of the shortcut in flight. A step that is not ``ok``
    turns its fired event into a ``steps`` fail; only a shortcut that ran to the
    end counts as a success in ``flush``. Only the step the shortcut expects next
    counts: a popup dismiss spliced in between is not one of its steps, and an
    insert the executor refused (``refused``) never runs any."""
    try:
        live = [
            ev
            for ev in getattr(ctx, "kbuf", None) or []
            if isinstance(ev, dict)
            and ev.get("kind") == "shortcut_fired"
            and not ev.get("refused")
            and int(ev.get("left") or 0) > 0
        ]
        if not live or not isinstance(entry, dict):
            return
        ev = live[-1]
        expect = ev.get("expect") or []
        at = len(expect) - int(ev["left"])
        if not 0 <= at < len(expect) or list(op_rid(entry.get("action") or {})) != list(expect[at]):
            return
        if entry.get("outcome") != "ok":
            ev.update(kind="shortcut_fail", why="steps", left=0)
            return
        ev["left"] = int(ev["left"]) - 1
    except Exception:
        logger.exception("knowledge shortcuts post_step failed")


def _load(raw: object, default: object) -> object:
    try:
        return json.loads(raw or "")
    except ValueError:
        return default


def snapshot(conn, facts) -> dict:
    """Active shortcuts (``[{id, name, start_key, steps, precondition}]``) and active edges."""
    from tools.mobile import knowledge_stage_edges

    out = []
    where = "status = 'active' AND invalid_at IS NULL AND start_key != ''"
    for row in knowledge_db.rows(conn, "shortcuts", where, (), knowledge_limits.SNAPSHOT_MAX_ROWS):
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "start_key": row["start_key"],
                "end_key": row["end_key"],
                "steps": _load(row["steps_json"], []),
                "precondition": _load(row["precondition_json"], {}),
            }
        )
    return {"shortcuts": out, "edges": knowledge_stage_edges.snapshot(conn, facts)}


def _row_of(conn, item: object) -> dict | None:
    parsed = knowledge_db.parse_item_id(item)
    if parsed is None or parsed[0] != "shortcuts":
        return None
    found = knowledge_db.rows(conn, "shortcuts", "id = ? AND invalid_at IS NULL", (parsed[1],), 1)
    return found[0] if found else None


def _apply(conn, run_id: str, ev: dict) -> None:
    row = _row_of(conn, ev.get("item"))
    if row is None:
        return
    if ev.get("kind") == "shortcut_fired":
        if ev.get("refused") or int(ev.get("left") or 0) > 0:
            return  # never ran to the end: not inserted, or the run stopped
        knowledge_db.upsert_counter(
            conn, "shortcuts", {"name": row["name"]}, {"success": 1}, run_id=run_id, why="shortcut fired"
        )
        return
    knowledge_db.upsert_counter(
        conn, "shortcuts", {"name": row["name"]}, {"fail": 1}, run_id=run_id, why="shortcut " + str(ev.get("why", ""))[:40]
    )
    if int(row.get("fail") or 0) + 1 >= knowledge_limits.STALE_AFTER_FAILS:
        knowledge_db.set_status(conn, "shortcuts", row["id"], "stale", run_id=run_id, why="shortcut failed repeatedly")


def flush(package: str, run_id: str, events: list) -> None:
    """Persist this run's shortcut fired/fail events (one connection, batched)."""
    mine = [e for e in events or [] if isinstance(e, dict) and e.get("kind") in ("shortcut_fired", "shortcut_fail")]
    if not mine:
        return
    conn = knowledge_db.open_rw(package)
    if conn is None:
        return
    try:
        for ev in mine:
            _apply(conn, run_id, ev)
    finally:
        conn.close()
