"""Learning stage locators: the ranked locator queue per element.

From each step's ``locator_used`` / ``locator_tried`` (the plug records them,
with the element's fingerprint as an ``fp:`` token) this creates or updates an
``elements`` row keyed by the fingerprint hash: per-locator hits and misses,
re-ranked best hit-ratio first (``LOCATOR_QUEUE_MAX``). The row is superseded
only when the ORDER changes; otherwise its counters are updated in place. A
heal (``heal:`` prefix) adds the new locator, so a renamed rid becomes a
superseding queue. A run already in a row's ``runs_json`` adds nothing.
"""

from __future__ import annotations

import json
import logging

from tools.mobile import knowledge_db, knowledge_limits, screen_key
from tools.mobile.knowledge_learn import StageResult
from tools.mobile.knowledge_plug_locators import FP_PREFIX, HEAL_PREFIX, parse_queue

logger = logging.getLogger(__name__)

_OPEN = "fp_hash = ? AND invalid_at IS NULL"
_KEEP = (
    "bound_field",
    "recheck_at",
    "missing_at",
    "trust",
    "confirmed",
    "contradicted",
)


def _fp(tried: list) -> dict | None:
    for item in tried:
        if isinstance(item, str) and item.startswith(FP_PREFIX):
            try:
                got = json.loads(item[len(FP_PREFIX) :])
            except ValueError:
                return None
            return got if isinstance(got, dict) and got else None
    return None


def collect(steps: list) -> dict:
    """``{(screen_key, fp_hash): obs}``; obs = fp, per-locator ``h``/``m``, new locators."""
    out: dict = {}
    for rec in steps:
        tried = list(rec.get("locator_tried") or [])
        fp = _fp(tried)
        used = str(rec.get("locator_used") or "")
        locs = [t for t in tried if isinstance(t, str) and not t.startswith(FP_PREFIX)]
        if fp is None or not rec.get("screen_key") or not (used or locs):
            continue
        obs = out.setdefault(
            (rec["screen_key"], screen_key.fp_hash(fp)),
            {"fp": fp, "counts": {}, "hits": 0, "misses": 0},
        )
        healed = used.startswith(HEAL_PREFIX)
        used = used[len(HEAL_PREFIX) :] if healed else used
        for loc in locs:
            obs["counts"].setdefault(loc, [0, 0])[1] += 1
        if used:
            obs["counts"].setdefault(used, [0, 0])[0] += 1
            obs["hits"] += 1
        else:
            obs["misses"] += 1
    return out


def rank(entries: list) -> list:
    """Best (laplace) hit-ratio first; ties keep their order. Deterministic."""
    keyed = [
        ((-(e["h"] + 1) / (e["h"] + e["m"] + 2)), i, e) for i, e in enumerate(entries)
    ]
    keyed.sort(key=lambda row: (row[0], row[1]))
    return [e for _, _, e in keyed][: knowledge_limits.LOCATOR_QUEUE_MAX]


def merge(queue: list, counts: dict) -> list:
    """*queue* plus this run's per-locator counts, re-ranked."""
    by_loc = {e["l"]: dict(e) for e in queue}
    order = [e["l"] for e in queue]
    for loc, (hits, misses) in counts.items():
        if loc not in by_loc:
            by_loc[loc] = {"l": loc, "h": 0, "m": 0}
            order.append(loc)
        by_loc[loc]["h"] += hits
        by_loc[loc]["m"] += misses
    return rank([by_loc[loc] for loc in order])


def _fresh(old: dict, run_id: str) -> bool:
    try:
        return run_id not in json.loads(str(old.get("runs_json") or "[]"))
    except (ValueError, TypeError):
        return True


def _update(conn, lc, old: dict, obs: dict) -> bool:
    if not _fresh(old, lc.run_id):
        return False
    before = parse_queue(old.get("locators_json"))
    after = merge(before, obs["counts"])
    hits = int(old.get("hits") or 0) + obs["hits"]
    misses = int(old.get("misses") or 0) + obs["misses"]
    why = "locator queue"
    if [e["l"] for e in after] != [e["l"] for e in before]:
        keep = {k: old[k] for k in _KEEP if k in old}
        new = {
            **keep,
            "screen_key": old["screen_key"],
            "fp_json": old["fp_json"],
            "fp_hash": old["fp_hash"],
            "locators_json": after,
            "hits": hits,
            "misses": misses,
            "status": "active",
            "app_version": str(getattr(lc.facts, "app_version", "") or ""),
            "runs_json": knowledge_db._bump_runs(old.get("runs_json"), lc.run_id),
        }
        return (
            knowledge_db.supersede(
                conn, "elements", old["id"], new, run_id=lc.run_id, why=why
            )
            is not None
        )
    return knowledge_db.set_status(
        conn,
        "elements",
        old["id"],
        str(old.get("status") or "active"),
        locators_json=after,
        hits=hits,
        misses=misses,
        runs_json=knowledge_db._bump_runs(old.get("runs_json"), lc.run_id),
        run_id=lc.run_id,
        why=why,
    )


def _squats(old: dict) -> bool:
    """An imported row holding this fp_hash: local evidence never merges into it,
    whatever its screen_key, so its locators cannot outrank what was observed."""
    return old.get("trust") == "imported"


def _fresh_values(lc, key: tuple, obs: dict, queue: list) -> dict:
    return {
        "screen_key": key[0],
        "fp_json": obs["fp"],
        "fp_hash": key[1],
        "locators_json": queue,
        "hits": obs["hits"],
        "misses": obs["misses"],
        "status": "active",
        "app_version": str(getattr(lc.facts, "app_version", "") or ""),
    }


def _create(conn, lc, key: tuple, obs: dict) -> bool:
    queue = merge([], obs["counts"])
    if not queue:
        return False
    got = knowledge_db.insert(
        conn,
        "elements",
        _fresh_values(lc, key, obs, queue),
        run_id=lc.run_id,
        why="locator queue",
    )
    return got is not None


def _replace_imported(conn, lc, old: dict, key: tuple, obs: dict) -> bool:
    """Supersede a squatting imported row with this run's own observation; the
    imported row's counters and locators never mix into local evidence."""
    queue = merge([], obs["counts"])
    if not queue:
        return False
    got = knowledge_db.supersede(
        conn,
        "elements",
        old["id"],
        _fresh_values(lc, key, obs, queue),
        run_id=lc.run_id,
        why="imported element superseded by local observation",
    )
    return got is not None


def run(conn, lc) -> StageResult:
    res = StageResult("locators")
    try:
        for key, obs in collect(list(lc.steps or [])).items():
            found = knowledge_db.rows(conn, "elements", _OPEN, (key[1],), 1)
            done = (
                _replace_imported(conn, lc, found[0], key, obs)
                if found and _squats(found[0])
                else _update(conn, lc, found[0], obs)
                if found
                else _create(conn, lc, key, obs)
            )
            res.wrote += 1 if done else 0
    except Exception as exc:
        logger.exception("knowledge locators stage failed")
        res.error = (type(exc).__name__ + ": " + str(exc))[:200]
    return res
