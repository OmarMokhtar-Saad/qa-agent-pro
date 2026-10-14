"""Stale-run collector.

``run_age``:
Seconds since this run's NEWEST activity.

Newest, not oldest: a run created three weeks ago whose last case was
checkpointed yesterday is a run somebody is still using, and taking
``created`` alone would delete it.

The lease heartbeat is deliberately NOT one of these timestamps. It is a
separate keep-reason in :func:`gc_stale_runs`, because a live lease is
always fresh -- folding it in here would make that branch unreachable, and
an unreachable branch cannot be tested or mutated.

NON-FINITE STAMPS ARE DISCARDED BEFORE ``max``, and that is load-bearing
rather than tidy. ``json.loads`` accepts a bare ``NaN`` literal and
``_read_json`` passes no ``parse_constant``, so a manifest can carry one --
and a NaN SURVIVES ``max`` when it is compared first, because every
comparison against NaN is False. The NaN then propagates into the age, and
in :func:`gc_stale_runs` the clock-skew, freshness and lease checks are all
comparisons, so all three are False and a seconds-old run is deleted. That
is not hypothetical: ``list_runs`` runs the GC by default, so merely
LISTING a tester's runs was what destroyed them.

A discarded stamp cannot fabricate freshness either -- the remaining stamps
are real, and if every stamp is junk the run is treated as being from NOW,
which keeps it. Keeping a run that should have been collected costs disk;
deleting one that should have been kept costs a tester their evidence, and
the four keep-reasons in the collector already say which way this leans.

``gc_stale_runs``:
Delete run directories older than *keep_s*. Never raises.

``{"error", "content": {"removed": [...], "kept": [{run_id, reason}],
"considered": n}}``.

THE KILL-SWITCH IS READ HERE, at the innermost function that deletes. This
removes a tester's own results -- an effect that outlives this process
exactly like a download or a spawn -- which is why
``tests/mobile/test_mobile_killswitch_surface.py`` lists ``shutil.rmtree(``
among its effect calls and why this function is not in its ``EXEMPT``.

FOUR independent reasons keep a run, because a GC that eats a resumable run
is worse than no GC at all:

* ``no_manifest`` -- unidentifiable, so not ours to delete;
* ``lease_live`` -- a chat is driving it right now, however old the files;
* ``clock_skew`` -- the newest timestamp is in the FUTURE, so a clock change
  must not be read as age;
* ``fresh`` -- inside the window. The threshold is strict: a run exactly
  *keep_s* old is kept.
"""

from __future__ import annotations

import logging
import math
import shutil
from pathlib import Path

from tools.mobile import paths, run_consts, run_io

logger = logging.getLogger("tools.mobile.run_store")


def _stat_stamps(root: Path) -> list[float]:
    stamps: list[float] = []
    try:
        stamps.append(float(root.stat().st_mtime))
    except OSError:  # pragma: no cover - the dir was just listed
        pass
    try:
        for child in (root / run_consts.CASES_DIR).glob("*.json"):
            stamps.append(float(child.stat().st_mtime))
    except OSError:  # pragma: no cover - defensive
        pass
    return stamps


def run_age(root: Path, manifest: dict, now: float) -> float:
    """Seconds since this run's NEWEST activity (non-finite stamps discarded)."""
    stamps = [float(manifest.get("created") or 0), *_stat_stamps(root)]
    finite = [stamp for stamp in stamps if math.isfinite(stamp)]
    if not finite:
        return 0.0
    return float(now) - max(finite)


def _lease_live(child: Path, moment: float) -> bool:
    lease = run_io.read_json(child / run_consts.LEASE_FILE)
    heartbeat = float(lease.get("heartbeat") or 0) if isinstance(lease, dict) else 0.0
    if math.isnan(heartbeat) or (isinstance(lease, dict) and lease.get("released_at")):
        return False
    return bool(heartbeat and (moment - heartbeat) <= run_consts.LEASE_STALE_S)


# A SECOND LAYER, deliberately, and not redundant with the
# filter in `run_age`. Every check below is a COMPARISON, and
# every comparison against NaN is False -- so a non-finite age
# falls through all of them to the `rmtree`. That is the one
# value where this function's default is to DELETE, which is
# exactly backwards for a collector whose four keep-reasons all
# exist because losing a resumable run costs a tester their
# evidence. `run_age` should never produce this now; if it ever
# does again, the run survives and says so instead of vanishing.
def _age_reason(age: float, keep_s: float) -> str | None:
    if not math.isfinite(age):
        return "unreadable_age"
    if age < 0:
        return "clock_skew"
    if age <= float(keep_s):
        return "fresh"
    return None


def _keep_reason(child: Path, moment: float, keep_s: float) -> str | None:
    """Why :func:`gc_stale_runs` must keep this run, or ``None`` to collect it."""
    manifest = run_io.read_json(child / run_consts.MANIFEST_FILE)
    if not isinstance(manifest, dict) or not manifest:
        return "no_manifest"
    if _lease_live(child, moment):
        return "lease_live"
    return _age_reason(run_age(child, manifest, moment), keep_s)


def _run_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [
        child
        for child in sorted(root.iterdir())
        if child.is_dir() and run_io.valid_run_id(child.name)
    ]


def _sweep_runs(
    root: Path, moment: float, keep_s: float
) -> tuple[list[str], list[dict], int]:
    """Delete every collectable run under *root*; ``(removed, kept, considered)``."""
    removed: list[str] = []
    kept: list[dict] = []
    children = _run_dirs(root)
    for child in children:
        reason = _keep_reason(child, moment, keep_s)
        if reason:
            kept.append({"run_id": child.name, "reason": reason})
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed.append(child.name)
    return removed, kept, len(children)


def _gc_report(removed: list[str], kept: list[dict], considered: int) -> dict:
    return {
        "error": None,
        "content": {"removed": removed, "kept": kept, "considered": considered},
    }


def gc_stale_runs(
    *, now: float | None = None, keep_s: float = run_consts.STALE_RUN_S
) -> dict:
    """Delete run directories older than *keep_s*. Never raises."""
    try:
        moment = run_io._now(now)
        removed, kept, considered = _sweep_runs(paths.sub("runs"), moment, keep_s)
        if removed:
            logger.info("mobile.run_store: collected %d stale run(s)", len(removed))
        return _gc_report(removed, kept, considered)
    except Exception as exc:
        logger.exception("mobile.run_store.gc_stale_runs failed")
        return {"error": str(exc), "content": None}


def _run_age(root: Path, manifest: dict, now: float) -> float:
    """Private name kept for the ``run_store`` facade; delegates at call time."""
    return run_age(root, manifest, now)
