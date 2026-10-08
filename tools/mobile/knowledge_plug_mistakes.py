"""Executor plug mistakes: field-binding refusal, text-landed stamp, screen anchors.

``pre_step`` reads only the in-memory snapshot (zero DB I/O). ``post_step`` stamps
the typed field on the entry and buffers one ``screen`` event per key per run;
``flush`` writes those as ``screens`` rows. ``snapshot`` loads the active
mistakes and element bindings.
"""

from __future__ import annotations

import json
import logging

from tools.mobile import (
    app_knowledge,
    knowledge_db,
    knowledge_hooks,
    knowledge_limits,
    screen_key,
)
from tools.untrusted import wrap_untrusted

logger = logging.getLogger(__name__)

PRIORITY = 10
_COLS = (
    "id",
    "screen_key",
    "element_fp",
    "bad_pattern",
    "symptom",
    "fix",
    "count",
    "trust",
    "status",
)


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


def pre_step(ctx, entry, step, screen):
    """Refuse typing a different field into an element bound to another one."""
    field, rid = _typed(step)
    if not field:
        return None
    bound = _bound(ctx, _key_of(ctx, entry, screen), rid)
    want = str(bound.get("field") or "")
    if not want or want == field:
        return None
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


def post_step(ctx, entry, before, after) -> None:
    """Stamp the typed field (success path) and buffer the screens seen."""
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
    shots = [
        ev for ev in events or [] if isinstance(ev, dict) and ev.get("e") == "screen"
    ]
    if not shots:
        return
    path = app_knowledge.db_path(package)
    if path is None or not path.exists():
        return  # an app with no store stays without one
    conn = knowledge_db.open_rw(package)
    if conn is None:
        return
    try:
        row = conn.execute(
            "SELECT app_version FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        version = str(row[0] or "") if row else ""
        for ev in shots:
            knowledge_db.insert(
                conn,
                "screens",
                {
                    "screen_key": str(ev.get("key") or ""),
                    "activity": str(ev.get("activity") or ""),
                    "anchors_json": json.dumps(list(ev.get("anchors") or [])),
                    "app_version": version,
                    "status": "active",
                    "source_run": run_id,
                },
                run_id=run_id,
                why="screen seen",
            )
    except Exception:
        logger.exception("knowledge mistakes flush failed")
    finally:
        conn.close()


def _mistakes(conn) -> dict:
    out: dict = {}
    where = "status = 'active' AND invalid_at IS NULL"
    for row in knowledge_db.rows(
        conn, "mistakes", where, (), knowledge_limits.SNAPSHOT_MAX_ROWS
    ):
        item = {col: row.get(col) for col in _COLS}
        item["table"] = "mistakes"
        out.setdefault(str(row.get("screen_key") or ""), []).append(item)
    return out


def _bindings(conn) -> dict:
    out: dict = {}
    where = "status = 'active' AND invalid_at IS NULL AND bound_field != ''"
    for row in knowledge_db.rows(
        conn, "elements", where, (), knowledge_limits.SNAPSHOT_MAX_ROWS
    ):
        try:
            rid = str(json.loads(row.get("fp_json") or "{}").get("rid") or "")
        except ValueError:
            continue
        if rid:
            out.setdefault(str(row.get("screen_key") or ""), {})[rid] = {
                "field": row["bound_field"],
                "id": row["id"],
            }
    return out


def snapshot(conn, facts) -> dict:
    return {"mistakes": _mistakes(conn), "bindings": _bindings(conn)}
