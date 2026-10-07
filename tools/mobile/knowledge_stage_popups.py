"""Learning stage popups: a screen that interrupts a flow and one tap dismisses.

Derived from steps: step i ended on screen X, which is not where step i started,
and step i+1 was a tap on X that returned to the key step i started on. The
signature is ``sha1(activity + sorted anchors of X)``; when the ``screens`` table
has no anchors for X the screen key itself stands in (it already hashes them).
A popup is upserted as a ``candidate`` (times seen, distinct runs); the gate
activates it at ``POPUP_MIN_RUNS`` distinct runs. A dismiss that looks
destructive is never recorded. Never raises.
"""

from __future__ import annotations

import json
import logging

from tools.mobile import knowledge_db, knowledge_limits
from tools.mobile.knowledge_learn import StageResult

logger = logging.getLogger(__name__)


def looks_destructive(text: object) -> bool:
    """True when the executor's destructive lexicon matches *text* (an rid or label)."""
    try:
        from tools.mobile import executor

        return executor.is_destructive(str(text or "").replace("_", " ").replace(".", " ").replace("/", " "))
    except Exception:
        logger.exception("knowledge popups: destructive check failed")
        return True


#: Longest stored signature; a clipped one can only merge look-alike popups.
_SIGNATURE_CLIP = 240


def _anchors(conn, key: str) -> list:
    found = knowledge_db.rows(conn, "screens", "screen_key = ? AND invalid_at IS NULL", (key,), 1)
    try:
        got = json.loads(found[0].get("anchors_json") or "[]") if found else []
    except ValueError:
        got = []
    return sorted(str(a) for a in got) if isinstance(got, list) else []


def signature(activity: object, anchors: list, dismiss_rid: str) -> str:
    """The popup identity, readable on purpose: activity plus sorted anchors, or
    the dismiss rid when no anchors are known. It is prose to the secret scan,
    so a popup whose anchors look secret is simply not learned."""
    basis = ",".join(anchors) if anchors else "dismiss:" + str(dismiss_rid or "")
    return (str(activity or "") + "|" + basis)[:_SIGNATURE_CLIP]


def find(steps: object) -> list:
    """``[(popup_key, activity, dismiss_rid, count)]`` found in one run's steps."""
    recs = list(steps or [])
    found: dict = {}
    for cur, nxt in zip(recs, recs[1:], strict=False):
        x = cur.get("after_screen_key")
        if not x or x == cur.get("screen_key") or nxt.get("screen_key") != x:
            continue
        if nxt.get("op") != "tap" or nxt.get("outcome") != "ok" or not nxt.get("target_rid"):
            continue
        if nxt.get("after_screen_key") != cur.get("screen_key") or str(x).startswith("sk1d:"):
            continue
        slot = (x, str(nxt.get("activity") or ""), nxt["target_rid"])
        found[slot] = found.get(slot, 0) + 1
    return [(k, a, r, n) for (k, a, r), n in sorted(found.items())]


def _upsert(conn, run_id: str, popup: tuple) -> int:
    key, activity, rid, count = popup
    sig = signature(activity, _anchors(conn, key), rid)
    found = knowledge_db.rows(conn, "popups", "signature = ? AND invalid_at IS NULL", (sig,), 1)
    if found:
        try:
            runs = json.loads(found[0].get("runs_json") or "[]")
        except ValueError:
            runs = []
        if run_id and run_id in runs:
            return 0
        got = knowledge_db.upsert_counter(
            conn, "popups", {"signature": sig}, {"times_seen": count, "runs_seen": 1},
            run_id=run_id, why="popup seen",
        )
        return 1 if got else 0
    dismiss = {"op": "tap", "rid": rid, "screen_key": key, "activity": activity}
    values = {
        "signature": sig,
        "dismiss_json": dismiss,
        "times_seen": count,
        "runs_seen": 1,
        "status": "candidate",
    }
    return 1 if knowledge_db.insert(conn, "popups", values, run_id=run_id, why="popup candidate") else 0


def run(conn, lc) -> StageResult:
    wrote, notes = 0, []
    for popup in find(lc.steps):
        if looks_destructive(popup[2]):
            notes.append("destructive-looking dismiss refused")
            continue
        wrote += _upsert(conn, lc.run_id, popup)
    ready = knowledge_db.rows(
        conn,
        "popups",
        "status = 'candidate' AND invalid_at IS NULL AND runs_seen >= ?",
        (knowledge_limits.POPUP_MIN_RUNS,),
        20,
    )
    if ready:
        notes.append("%d popup candidates gate-ready" % len(ready))
    return StageResult("popups", wrote=wrote, notes=notes[:5])
