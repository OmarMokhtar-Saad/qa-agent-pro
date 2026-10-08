"""Learning stage mistakes: failure signatures, the credit/fix contrast, field binding.

From the run's ``StepRec`` list (never screen text, never typed values) this stage
derives deterministic failure signatures and upserts ``mistakes`` rows, and learns
which field an element takes from a successful ``type`` (``elements.bound_field``).
Credit goes to the first non-ok step of a failure episode plus its predecessor; the
fix is the later OK retry on the same screen_key (the contrast). A mistake is a
candidate until the gate sees MISTAKE_ACTIVE_RUNS distinct runs; a tester
correction is trusted at once. Text is built from templates and rid/field NAMES only.
A degraded screen key (no stable anchors) learns nothing.
"""

from __future__ import annotations

import json

from tools.mobile import knowledge_db, knowledge_limits, screen_key
from tools.mobile.knowledge_learn import StageResult

OK_OUTCOMES = ("ok", "assert_pass", "visual_check", "done")
TAP_OPS = ("tap", "tap_text", "click", "long_press")
_OUTCOME_SIGNATURE = {
    "wait_timeout": "timeout",
    "missing_element": "element_missing",
    "route_mismatch": "wrong_screen",
}
_SYMPTOM = {
    "field_collision": "two different fields were typed into %s",
    "text_not_landed": "typing into %s did not land",
    "dead_tap": "tap on %s left the screen unchanged",
    "wrong_screen": "%s led to a different screen than expected",
    "timeout": "waiting after %s timed out",
    "element_missing": "%s was not found on this screen",
    "tester_correction": "a tester corrected the step on %s",
}
_NO_TARGET = "the screen"


def _degraded(key: str) -> bool:
    return not key or key.startswith("sk1d:")


def _name(rec: dict) -> str:
    return str(rec.get("target_rid") or "") or _NO_TARGET


def _ok(rec: dict) -> bool:
    return str(rec.get("outcome") or "") in OK_OUTCOMES


def _typed_failed(rec: dict) -> bool:
    if rec.get("op") != "type":
        return False
    return rec.get("landed") is False or rec.get("outcome") == "device_error"


def _dead_tap(rec: dict) -> bool:
    after = str(rec.get("after_screen_key") or "")
    if rec.get("op") not in TAP_OPS or not after:
        return False
    return rec.get("outcome") in ("ok", "no_change") and after == rec.get("screen_key")


def signature_of(rec: dict) -> str:
    """The failure signature of one step, or ``""`` (collisions need the run)."""
    if rec.get("tester_correction"):
        return "tester_correction"
    if _typed_failed(rec):
        return "text_not_landed"
    if _dead_tap(rec):
        return "dead_tap"
    return _OUTCOME_SIGNATURE.get(str(rec.get("outcome") or ""), "")


def _collision(rec: dict, seen: dict) -> bool:
    field = str(rec.get("text_field") or "")
    rid = str(rec.get("target_rid") or "")
    if rec.get("op") != "type" or not field or not rid:
        return False
    first = seen.setdefault((rec.get("screen_key"), rid), field)
    return first != field


def _fix_of(steps: list, i: int, sig: str) -> dict | None:
    """The later OK step on the same screen_key that contrasts with the failure."""
    rec = steps[i]
    for later in steps[i + 1 :]:
        if later.get("screen_key") != rec.get("screen_key") or not _ok(later):
            continue
        if sig == "dead_tap":
            moved = later.get("after_screen_key") not in ("", later.get("screen_key"))
            if moved and later.get("target_rid") != rec.get("target_rid"):
                return later
        elif later.get("op") == rec.get("op") and later.get("target_rid") == rec.get(
            "target_rid"
        ):
            return later
    return None


def find_hits(steps: list) -> list:
    """One hit per failure episode: ``{sig, rec, prev, fix}`` in step order."""
    hits: list = []
    seen: dict = {}
    last = ("", "", "")
    for i, rec in enumerate(steps):
        sig = "field_collision" if _collision(rec, seen) else signature_of(rec)
        here = (sig, rec.get("screen_key"), rec.get("target_rid"))
        if sig and here != last:
            prev = steps[i - 1] if i else None
            hits.append(
                {"sig": sig, "rec": rec, "prev": prev, "fix": _fix_of(steps, i, sig)}
            )
        last = here if sig else ("", "", "")
    return hits


def texts(hit: dict) -> tuple:
    """``(symptom, fix)`` from templates and names only."""
    rec, prev, fix = hit["rec"], hit["prev"], hit["fix"]
    symptom = _SYMPTOM[hit["sig"]] % _name(rec)
    if prev is not None and prev.get("op"):
        symptom += " (after %s on %s)" % (prev.get("op"), _name(prev))
    fixed = ""
    if fix is not None:
        fixed = "a later %s on %s worked on this screen" % (fix.get("op"), _name(fix))
    return symptom[:200], fixed[:200]


def _find(conn, table: str, where: str, args: tuple):
    got = knowledge_db.rows(conn, table, where + " AND invalid_at IS NULL", args, 1)
    return got[0] if got else None


def _credit(conn, hit: dict, run_id: str) -> int:
    """Upsert one mistake row; the number of rows written (0 or 1)."""
    rec = hit["rec"]
    key = {
        "screen_key": rec["screen_key"],
        "element_fp": str(rec.get("target_rid") or ""),
        "bad_pattern": hit["sig"],
    }
    tester = hit["sig"] == "tester_correction"
    old = _find(
        conn,
        "mistakes",
        "screen_key = ? AND element_fp = ? AND bad_pattern = ?",
        tuple(key.values()),
    )
    if old is None:
        symptom, fix = texts(hit)
        extra = {"trust": "tester", "status": "active"} if tester else {}
        values = {
            **key,
            "symptom": symptom,
            "fix": fix,
            "count": 1,
            "runs_seen": 1,
            **extra,
        }
        return int(
            knowledge_db.insert(conn, "mistakes", values, run_id=run_id, why="mistake")
            is not None
        )
    deltas = {"count": 1, "runs_seen": 0 if run_id in _runs(old) else 1}
    row_id = knowledge_db.upsert_counter(
        conn, "mistakes", key, deltas, run_id=run_id, why="mistake"
    )
    if row_id is not None and tester and old["status"] != "active":
        knowledge_db.set_status(
            conn,
            "mistakes",
            row_id,
            "active",
            trust="tester",
            run_id=run_id,
            why="mistake tester",
        )
    return int(row_id is not None)


def _runs(row: dict) -> list:
    try:
        got = json.loads(str(row.get("runs_json") or "[]"))
        return got if isinstance(got, list) else []
    except ValueError:
        return []


def _binding_fp(rid: str, key: str) -> tuple:
    fp = {"rid": rid, "kind": "binding", "screen": key}
    return fp, screen_key.fp_hash(fp)


def learned_bindings(steps: list) -> dict:
    """``{(screen_key, rid): (field, tester)}`` from landed, OK ``type`` steps. An
    element typed with two different fields in one run binds nothing."""
    out: dict = {}
    clash: set = set()
    for rec in steps:
        field, rid = str(rec.get("text_field") or ""), str(rec.get("target_rid") or "")
        if (
            rec.get("op") != "type"
            or rec.get("landed") is not True
            or not (field and rid)
        ):
            continue
        if _degraded(str(rec.get("screen_key") or "")) or not _ok(rec):
            continue
        slot = (rec["screen_key"], rid)
        if slot in out and out[slot][0] != field:
            clash.add(slot)
        out[slot] = (
            field,
            bool(rec.get("tester_correction")) or out.get(slot, ("", False))[1],
        )
    return {slot: val for slot, val in out.items() if slot not in clash}


def _distinct(row: dict) -> int:
    return len(set(_runs(row)))


def _promote(conn, row_id: int, tester: bool, run_id: str) -> None:
    row = _find(conn, "elements", "id = ?", (row_id,))
    if row is None or row["status"] != "candidate":
        return
    if tester or _distinct(row) >= knowledge_limits.MISTAKE_ACTIVE_RUNS:
        cols = {"trust": "tester"} if tester else {}
        knowledge_db.set_status(
            conn,
            "elements",
            row_id,
            "active",
            run_id=run_id,
            why="binding confirmed",
            **cols,
        )


def _contradict(conn, old: dict, run_id: str) -> None:
    """A different field landed where an ACTIVE binding says otherwise."""
    knowledge_db.upsert_counter(
        conn,
        "elements",
        {"fp_hash": old["fp_hash"]},
        {"contradicted": 1},
        run_id=run_id,
        why="binding conflict",
    )
    hot = int(old["contradicted"] or 0) + 1
    if hot >= knowledge_limits.CONTRADICT_FLOOR and hot > int(old["confirmed"] or 0):
        knowledge_db.set_status(
            conn,
            "elements",
            old["id"],
            "stale",
            run_id=run_id,
            why="binding contradicted",
        )


def _bind(conn, slot: tuple, val: tuple, run_id: str) -> int:
    key, rid = slot
    field, tester = val
    fp, digest = _binding_fp(rid, key)
    old = _find(conn, "elements", "fp_hash = ?", (digest,))
    if old is None:
        values = {
            "screen_key": key,
            "fp_json": fp,
            "fp_hash": digest,
            "bound_field": field,
            "locators_json": "[]",
        }
        row_id = knowledge_db.insert(
            conn, "elements", values, run_id=run_id, why="binding"
        )
    elif old["bound_field"] == field:
        row_id = knowledge_db.upsert_counter(
            conn,
            "elements",
            {"fp_hash": digest},
            {"confirmed": 1},
            run_id=run_id,
            why="binding",
        )
    elif old["status"] == "active":
        _contradict(conn, old, run_id)
        return 0
    else:
        values = {
            "screen_key": key,
            "fp_json": fp,
            "fp_hash": digest,
            "bound_field": field,
            "locators_json": "[]",
        }
        row_id = knowledge_db.supersede(
            conn, "elements", old["id"], values, run_id=run_id, why="binding changed"
        )
    if row_id is not None:
        _promote(conn, row_id, tester, run_id)
    return int(row_id is not None)


def run(conn, lc) -> StageResult:
    """Never raises past the pipeline; one transaction per call (the pipeline's)."""
    steps = [
        s for s in (lc.steps or []) if not _degraded(str(s.get("screen_key") or ""))
    ]
    result = StageResult("mistakes")
    for hit in find_hits(steps):
        result.wrote += _credit(conn, hit, lc.run_id)
    for slot, val in learned_bindings(steps).items():
        result.wrote += _bind(conn, slot, val, lc.run_id)
    result.notes.append("%d mistake/binding row(s)" % result.wrote)
    return result
