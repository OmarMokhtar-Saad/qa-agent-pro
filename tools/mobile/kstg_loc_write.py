"""Element row writes for the locator queue (split out of knowledge_stage_locators)."""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from tools.mobile import knowledge_db
from tools.mobile.knowledge_plug_locators import parse_queue
from tools.mobile.kstg_loc_obs import merge

_KEEP = (
    "bound_field",
    "recheck_at",
    "missing_at",
    "trust",
    "confirmed",
    "contradicted",
)


class Item(NamedTuple):
    lc: Any
    key: tuple
    obs: dict


class Plan(NamedTuple):
    old: dict
    item: Item
    after: list
    hits: int
    misses: int


def _fresh(old: dict, run_id: str) -> bool:
    try:
        return run_id not in json.loads(str(old.get("runs_json") or "[]"))
    except (ValueError, TypeError):
        return True


def _version(lc) -> str:
    return str(getattr(lc.facts, "app_version", "") or "")


def _reorder(conn, plan: Plan) -> bool:
    old, lc = plan.old, plan.item.lc
    keep = {k: old[k] for k in _KEEP if k in old}
    new = {
        **keep,
        "screen_key": old["screen_key"],
        "fp_json": old["fp_json"],
        "fp_hash": old["fp_hash"],
        "locators_json": plan.after,
        "hits": plan.hits,
        "misses": plan.misses,
        "status": "active",
        "app_version": _version(lc),
        "runs_json": knowledge_db._bump_runs(old.get("runs_json"), lc.run_id),
    }
    why = "locator queue"
    replace = knowledge_db.Replace(old["id"], new, run_id=lc.run_id, why=why)
    return knowledge_db.supersede(conn, "elements", replace) is not None


def _in_place(conn, plan: Plan) -> bool:
    old, lc = plan.old, plan.item.lc
    counters = {
        "locators_json": plan.after,
        "hits": plan.hits,
        "misses": plan.misses,
        "runs_json": knowledge_db._bump_runs(old.get("runs_json"), lc.run_id),
    }
    status = knowledge_db.Status(
        old["id"],
        str(old.get("status") or "active"),
        counters,
        run_id=lc.run_id,
        why="locator queue",
    )
    return knowledge_db.set_status(conn, "elements", status)


def update(conn, old: dict, item: Item) -> bool:
    if not _fresh(old, item.lc.run_id):
        return False
    before = parse_queue(old.get("locators_json"))
    after = merge(before, item.obs["counts"])
    hits = int(old.get("hits") or 0) + item.obs["hits"]
    misses = int(old.get("misses") or 0) + item.obs["misses"]
    plan = Plan(old, item, after, hits, misses)
    if [e["l"] for e in after] != [e["l"] for e in before]:
        return _reorder(conn, plan)
    return _in_place(conn, plan)


def squats(old: dict) -> bool:
    """An imported row holding this fp_hash: local evidence never merges into it,
    whatever its screen_key, so its locators cannot outrank what was observed."""
    return old.get("trust") == "imported"


def fresh_values(item: Item, queue: list) -> dict:
    obs = item.obs
    return {
        "screen_key": item.key[0],
        "fp_json": obs["fp"],
        "fp_hash": item.key[1],
        "locators_json": queue,
        "hits": obs["hits"],
        "misses": obs["misses"],
        "status": "active",
        "app_version": _version(item.lc),
    }


def create(conn, item: Item) -> bool:
    queue = merge([], item.obs["counts"])
    if not queue:
        return False
    write = knowledge_db.Write(
        fresh_values(item, queue), run_id=item.lc.run_id, why="locator queue"
    )
    return knowledge_db.insert(conn, "elements", write) is not None


# Supersede a squatting imported row with this run's own observation; the
# imported row's counters and locators never mix into local evidence.
def replace_imported(conn, old: dict, item: Item) -> bool:
    queue = merge([], item.obs["counts"])
    if not queue:
        return False
    replace = knowledge_db.Replace(
        old["id"],
        fresh_values(item, queue),
        run_id=item.lc.run_id,
        why="imported element superseded by local observation",
    )
    return knowledge_db.supersede(conn, "elements", replace) is not None
