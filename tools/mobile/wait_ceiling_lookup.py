"""Where a wait's learned samples live and how a spec takes the learned bound."""

from __future__ import annotations

import time
from typing import Any

from tools.mobile import knowledge_plug_timings as plug
from tools.mobile import wait_ceiling
from tools.mobile.action_consts import WAIT_UNTIL_OPS


def samples(ctx: Any, target: Any, screen: Any) -> list:
    """The learned OK-wait samples (ms) of a wait on *screen*; ``[]`` when none.

    *target* is ``(op, target_rid)`` (a bare op means no rid): rows are stored per
    target, so the lookup must name the same rid the learner recorded.
    """
    op, rid = (target, "") if isinstance(target, str) else target
    if op not in WAIT_UNTIL_OPS:
        return []
    snap = (getattr(ctx, "knowledge", None) or {}).get("snap") or {}
    waits = snap.get("waits") or {}
    if not waits:
        return []
    return list(waits.get((plug.key_of(ctx, screen), op, rid)) or [])


def bounded(spec: Any, learned: list) -> Any:
    """*spec* with its ms ceiling replaced by the learned one."""
    kind, text, ms = spec
    return type(spec)(kind, text, wait_ceiling.ceiling_ms(learned, ms))


def mark_slow(entry: dict, learned: list, began: float) -> None:
    """Note on *entry* a met wait that ran past 2 x p95."""
    note = wait_ceiling.slow_note(int((time.monotonic() - began) * 1000), learned)
    if note and entry.get("outcome") == "ok":
        entry["slower_than_usual"] = note
        entry["detail"] = (str(entry.get("detail") or "") + " (" + note + ")").strip()


def mark_learned(entry: dict, declared_ms: int, bound_ms: int) -> None:
    """Say on a wait that ran out at a LEARNED bound (shorter than declared) that it did.

    Without this a timeout at 2 s of a 30 s declared wait looks like a declared limit.
    """
    detail = str(entry.get("detail") or "")
    missed = entry.get("outcome") == "wait_timeout" or "did not change" in detail
    if bound_ms >= declared_ms or not missed:
        return
    entry["learned_limit_ms"] = bound_ms
    note = "learned limit %g s, declared %g s" % (bound_ms / 1000, declared_ms / 1000)
    entry["detail"] = (detail + " (" + note + ")").strip()
