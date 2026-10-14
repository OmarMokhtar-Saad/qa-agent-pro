"""Field binding learned from landed type steps (split out of knowledge_stage_mistakes)."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile import knowledge_db, knowledge_limits, screen_key
from tools.mobile.kstg_mist_rows import Lookup, distinct, find_open
from tools.mobile.kstg_mist_sig import degraded, ok


class Bind(NamedTuple):
    row_id: int
    tester: bool
    run_id: str


class BindSlot(NamedTuple):
    slot: tuple
    val: tuple
    run_id: str


def _binding_fp(rid: str, key: str) -> tuple:
    fp = {"rid": rid, "kind": "binding", "screen": key}
    return fp, screen_key.fp_hash(fp)


def _typed(rec: dict) -> tuple | None:
    """``(screen_key, rid, field)`` of a landed, OK, stable ``type`` step."""
    field, rid = str(rec.get("text_field") or ""), str(rec.get("target_rid") or "")
    if rec.get("op") != "type" or rec.get("landed") is not True:
        return None
    if not (field and rid) or degraded(str(rec.get("screen_key") or "")):
        return None
    return (rec["screen_key"], rid, field) if ok(rec) else None


def learned_bindings(steps: list) -> dict:
    """``{(screen_key, rid): (field, tester)}`` from landed, OK ``type`` steps. An
    element typed with two different fields in one run binds nothing."""
    out: dict = {}
    clash: set = set()
    for rec in steps:
        got = _typed(rec)
        if got is None:
            continue
        slot, field = (got[0], got[1]), got[2]
        if slot in out and out[slot][0] != field:
            clash.add(slot)
        tester = bool(rec.get("tester_correction")) or out.get(slot, ("", False))[1]
        out[slot] = (field, tester)
    return {slot: val for slot, val in out.items() if slot not in clash}


def promote(conn, b: Bind) -> None:
    row = find_open(conn, Lookup("elements", "id = ?", (b.row_id,)))
    if row is None or row["status"] != "candidate":
        return
    if b.tester or distinct(row) >= knowledge_limits.MISTAKE_ACTIVE_RUNS:
        cols = {"trust": "tester"} if b.tester else {}
        status = knowledge_db.Status(
            b.row_id, "active", cols, run_id=b.run_id, why="binding confirmed"
        )
        knowledge_db.set_status(conn, "elements", status)


def _flag(conn, old: dict, run_id: str) -> None:
    counter = knowledge_db.Counter(
        {"fp_hash": old["fp_hash"]},
        {"contradicted": 1},
        run_id=run_id,
        why="binding conflict",
    )
    knowledge_db.upsert_counter(conn, "elements", counter)


def _stale_if_hot(conn, old: dict, run_id: str) -> None:
    hot = int(old["contradicted"] or 0) + 1
    if hot >= knowledge_limits.CONTRADICT_FLOOR and hot > int(old["confirmed"] or 0):
        status = knowledge_db.Status(
            old["id"], "stale", None, run_id=run_id, why="binding contradicted"
        )
        knowledge_db.set_status(conn, "elements", status)


def _contradict(conn, old: dict, run_id: str) -> None:
    """A different field landed where an ACTIVE binding says otherwise."""
    _flag(conn, old, run_id)
    _stale_if_hot(conn, old, run_id)


def _row_values(bs: BindSlot) -> dict:
    key, rid = bs.slot
    fp, digest = _binding_fp(rid, key)
    return {
        "screen_key": key,
        "fp_json": fp,
        "fp_hash": digest,
        "bound_field": bs.val[0],
        "locators_json": "[]",
    }


def _clashes(old: dict | None, bs: BindSlot) -> bool:
    return (
        old is not None
        and old["bound_field"] != bs.val[0]
        and old["status"] == "active"
    )


def _apply(conn, old: dict | None, bs: BindSlot):
    if old is None:
        write = knowledge_db.Write(_row_values(bs), run_id=bs.run_id, why="binding")
        return knowledge_db.insert(conn, "elements", write)
    if old["bound_field"] == bs.val[0]:
        digest = _binding_fp(bs.slot[1], bs.slot[0])[1]
        counter = knowledge_db.Counter(
            {"fp_hash": digest}, {"confirmed": 1}, run_id=bs.run_id, why="binding"
        )
        return knowledge_db.upsert_counter(conn, "elements", counter)
    repl = knowledge_db.Replace(
        old["id"], _row_values(bs), run_id=bs.run_id, why="binding changed"
    )
    return knowledge_db.supersede(conn, "elements", repl)


def bind(conn, bs: BindSlot) -> int:
    digest = _binding_fp(bs.slot[1], bs.slot[0])[1]
    old = find_open(conn, Lookup("elements", "fp_hash = ?", (digest,)))
    if _clashes(old, bs):
        _contradict(conn, old, bs.run_id)
        return 0
    row_id = _apply(conn, old, bs)
    if row_id is not None:
        promote(conn, Bind(row_id, bs.val[1], bs.run_id))
    return int(row_id is not None)
