"""Learning stage shortcuts: mine repeated OK step sequences, seed from routes.

A window of ``SHORTCUT_MIN_LEN..SHORTCUT_MAX_LEN`` consecutive OK ``tap``/``type``
steps (each ends on the next one's start screen, an rid on every step) is a
shortcut candidate named by a hash of its start key, end key and steps. Typed
text is NEVER stored: a ``type`` step keeps its rid and field name only and the
text becomes ``{arg_n}`` (``args_json``). A sequence that names a credential
field or trips the secret scrub is refused. A candidate that has been seen in
``SHORTCUT_MIN_RUNS`` distinct runs is gate-ready (the gate activates it); until
then, and always for a candidate, nothing replays it. Saved routes are folded in
through ``knowledge_routes_bridge`` (read only). Never raises.
"""

from __future__ import annotations

import hashlib
import json
import logging

from tools.mobile import (
    app_knowledge,
    knowledge_db,
    knowledge_limits,
    knowledge_routes_bridge,
)
from tools.mobile.knowledge_learn import StageResult

logger = logging.getLogger(__name__)

NEW_PER_RUN = 10
MAX_WINDOWS = 300
_OPS = ("tap", "type")


def _linked(a: dict, b: dict) -> bool:
    return a.get("after_screen_key") == b.get("screen_key")


def _eligible(rec: dict) -> bool:
    return (
        rec.get("outcome") == "ok"
        and rec.get("op") in _OPS
        and bool(rec.get("target_rid"))
        and not str(rec.get("screen_key", "")).startswith("sk1d:")
        and bool(rec.get("after_screen_key"))
    )


def chains(steps: object) -> list:
    """Maximal runs of eligible steps where each ends on the next one's start screen."""
    out: list = []
    cur: list = []
    for rec in list(steps or []):
        if not _eligible(rec):
            if cur:
                out.append(cur)
            cur = []
            continue
        if cur and not _linked(cur[-1], rec):
            out.append(cur)
            cur = []
        cur.append(rec)
    if cur:
        out.append(cur)
    return out


def _shape(window: list) -> tuple[list, dict]:
    """The stored steps and args of *window*; typed text becomes ``{arg_n}``."""
    steps, args = [], {}
    for rec in window:
        step = {"op": rec["op"], "rid": rec["target_rid"]}
        if rec["op"] == "type":
            name = "arg_%d" % (len(args) + 1)
            step["arg"] = name
            field = str(rec.get("text_field") or "")
            if field:
                step["field"] = field
            args[name] = {"field": field}
        steps.append(step)
    return steps, args


def secret_refusal(steps: list) -> str:
    """Why this sequence must not be stored, or ``""``."""
    from tools.mobile import actions

    for step in steps:
        if step["op"] == "type":
            probe = {
                "op": "type",
                "target": {"rid": step["rid"]},
                "field": step.get("field", ""),
            }
            if actions.is_credential_action(probe):
                return "credential field"
    return app_knowledge.secret_reason(json.dumps(steps), (), "")


def candidates(steps: object) -> tuple[list, int]:
    """``([(name, start, end, steps, args)], refused)`` mined from one run, longest first."""
    seen: dict = {}
    refused = 0
    lo, hi = knowledge_limits.SHORTCUT_MIN_LEN, knowledge_limits.SHORTCUT_MAX_LEN
    for chain in chains(steps):
        for size in range(min(hi, len(chain)), lo - 1, -1):
            for i in range(len(chain) - size + 1):
                window = chain[i : i + size]
                start, end = window[0]["screen_key"], window[-1]["after_screen_key"]
                if start == end:
                    continue
                shaped, args = _shape(window)
                if secret_refusal(shaped):
                    refused += 1
                    continue
                digest = hashlib.sha1(
                    json.dumps([start, end, shaped], sort_keys=True).encode()
                ).hexdigest()
                seen.setdefault("mined-" + digest[:12], (start, end, shaped, args))
                if len(seen) >= MAX_WINDOWS:
                    break
    return [(n, *v) for n, v in seen.items()], refused


def _runs(row: dict) -> list:
    try:
        got = json.loads(row.get("runs_json") or "[]")
        return got if isinstance(got, list) else []
    except ValueError:
        return []


def _make_room(conn, run_id: str) -> None:
    (have,) = conn.execute("SELECT COUNT(*) FROM shortcuts").fetchone()
    if have < knowledge_limits.MAX_ROWS["shortcuts"]:
        return
    row = conn.execute(
        "SELECT id FROM shortcuts WHERE invalid_at IS NULL AND status = 'candidate'"
        " AND origin = 'mined' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if row is not None:
        knowledge_db.set_status(
            conn, "shortcuts", row["id"], "retired", run_id=run_id, why="shortcut cap"
        )


def _insert(conn, run_id: str, cand: tuple) -> int:
    name, start, end, steps, args = cand
    _make_room(conn, run_id)
    values = {
        "name": name,
        "origin": "mined",
        "status": "candidate",
        "steps_json": steps,
        "args_json": args,
        "precondition_json": {"screen_key": start, "rids": [steps[0]["rid"]]},
        "start_key": start,
        "end_key": end,
    }
    return (
        1
        if knowledge_db.insert(
            conn, "shortcuts", values, run_id=run_id, why="shortcut mined"
        )
        else 0
    )


def _mine(conn, lc) -> tuple[int, int, int]:
    """``(rows written, gate-ready, refused)``."""
    found, refused = candidates(lc.steps)
    wrote, ready, fresh = 0, 0, 0
    for cand in found:
        have = knowledge_db.rows(
            conn, "shortcuts", "name = ? AND invalid_at IS NULL", (cand[0],), 1
        )
        if have:
            if lc.run_id not in _runs(have[0]):
                got = knowledge_db.upsert_counter(
                    conn,
                    "shortcuts",
                    {"name": cand[0]},
                    {},
                    run_id=lc.run_id,
                    why="shortcut seen",
                )
                wrote += 1 if got else 0
            if (
                have[0]["status"] == "candidate"
                and len(_runs(have[0])) + 1 >= knowledge_limits.SHORTCUT_MIN_RUNS
            ):
                ready += 1
        elif fresh < NEW_PER_RUN:
            fresh += 1
            wrote += _insert(conn, lc.run_id, cand)
    return wrote, ready, refused


def run(conn, lc) -> StageResult:
    notes: list = []
    wrote, ready, refused = _mine(conn, lc)
    wrote += knowledge_routes_bridge.seed_shortcuts_from_routes(
        lc.package, conn, lc.run_id
    )
    woke = knowledge_routes_bridge.activate_seeds(conn, lc.package, lc.steps, lc.run_id)
    wrote += woke
    if ready:
        notes.append("%d shortcut candidates gate-ready" % ready)
    if refused:
        notes.append("%d sequences refused (secret)" % refused)
    if woke:
        notes.append("%d route seeds activated" % woke)
    return StageResult("shortcuts", wrote=wrote, notes=notes)
