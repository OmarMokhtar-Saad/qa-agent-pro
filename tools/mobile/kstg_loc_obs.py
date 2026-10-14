"""Locator observations of one run: collect, rank, merge (split out of knowledge_stage_locators)."""

from __future__ import annotations

import json

from tools.mobile import knowledge_limits, screen_key
from tools.mobile.knowledge_plug_locators import FP_PREFIX, HEAL_PREFIX


def _fp(tried: list) -> dict | None:
    for item in tried:
        if isinstance(item, str) and item.startswith(FP_PREFIX):
            try:
                got = json.loads(item[len(FP_PREFIX) :])
            except ValueError:
                return None
            return got if isinstance(got, dict) and got else None
    return None


def _read(rec: dict) -> tuple | None:
    tried = list(rec.get("locator_tried") or [])
    fp = _fp(tried)
    used = str(rec.get("locator_used") or "")
    locs = [t for t in tried if isinstance(t, str) and not t.startswith(FP_PREFIX)]
    if fp is None or not rec.get("screen_key") or not (used or locs):
        return None
    return fp, used, locs


def _tally(obs: dict, used: str, locs: list) -> None:
    healed = used.startswith(HEAL_PREFIX)
    used = used[len(HEAL_PREFIX) :] if healed else used
    for loc in locs:
        obs["counts"].setdefault(loc, [0, 0])[1] += 1
    if used:
        obs["counts"].setdefault(used, [0, 0])[0] += 1
        obs["hits"] += 1
    else:
        obs["misses"] += 1


def collect(steps: list) -> dict:
    """``{(screen_key, fp_hash): obs}``; obs = fp, per-locator ``h``/``m``, new locators."""
    out: dict = {}
    for rec in steps:
        got = _read(rec)
        if got is None:
            continue
        fp, used, locs = got
        obs = out.setdefault(
            (rec["screen_key"], screen_key.fp_hash(fp)),
            {"fp": fp, "counts": {}, "hits": 0, "misses": 0},
        )
        _tally(obs, used, locs)
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
