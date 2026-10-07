"""Executor hook registry: four seams, every one total (an exception => none).

``pre_step`` / ``resolve_target`` return a ``Directive``; ``step_wait`` returns
ms; ``post_step`` merges in-memory events (``ctx.kbuf``) into the step entry.
Plug modules (``PLUGS``) expose ``PRIORITY`` and any of the seam functions plus
an optional ``snapshot(conn, facts)``; they are imported lazily once, and a
missing or broken one is logged and skipped. NOTHING here touches the database:
the hot path has zero DB I/O.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass, field

from tools.mobile import knowledge_limits

logger = logging.getLogger(__name__)

KINDS = ("none", "refuse", "insert_steps", "replace_target", "stop")

PLUGS = (
    "knowledge_plug_mistakes",
    "knowledge_plug_popups",
    "knowledge_plug_shortcuts",
    "knowledge_plug_locators",
    "knowledge_plug_timings",
    "knowledge_feedback",
)

_LOADED: list | None = None


@dataclass
class Directive:
    kind: str = "none"
    reason: str = ""
    refuse_item: str = ""
    steps: list = field(default_factory=list)
    target: object = None
    fired: object = None


NONE = Directive()


def plugs() -> list:
    """Loaded plug modules by ascending PRIORITY (imported once)."""
    global _LOADED
    if _LOADED is None:
        found = []
        for name in PLUGS:
            try:
                mod = importlib.import_module("tools.mobile." + name)
                found.append((int(getattr(mod, "PRIORITY", 100)), name, mod))
            except Exception:
                logger.exception("knowledge plug %s skipped", name)
        found.sort(key=lambda row: (row[0], row[1]))
        _LOADED = [row[2] for row in found]
    return _LOADED


def reset() -> None:
    """Forget the loaded plugs (tests)."""
    global _LOADED
    _LOADED = None


def _directive(got: object) -> Directive:
    if isinstance(got, Directive) and got.kind in KINDS:
        return got
    return NONE


async def _first(seam: str, *args) -> Directive:
    for mod in plugs():
        fn = getattr(mod, seam, None)
        if fn is None:
            continue
        try:
            got = fn(*args)
            if inspect.isawaitable(got):
                got = await got
        except Exception:
            logger.exception("knowledge plug %s.%s failed", mod.__name__, seam)
            continue
        out = _directive(got)
        if out.kind != "none":
            return out
    return NONE


async def pre_step(ctx, entry, step, screen) -> Directive:
    """Popup dismiss, precondition, field-binding refusal, shortcut."""
    try:
        return await _first("pre_step", ctx, entry, step, screen)
    except Exception:
        logger.exception("knowledge pre_step failed")
        return NONE


async def resolve_target(ctx, step, screen, default) -> Directive:
    """Ranked locator queue, similarity heal, missing@vX."""
    try:
        return await _first("resolve_target", ctx, step, screen, default)
    except Exception:
        logger.exception("knowledge resolve_target failed")
        return NONE


def step_wait(ctx, step, screen) -> int:
    """Adaptive wait in ms, capped at the executor's MAX_WAIT_MS; 0 = none."""
    try:
        from tools.mobile import actions

        best = 0
        for mod in plugs():
            fn = getattr(mod, "step_wait", None)
            if fn is None:
                continue
            try:
                best = max(best, int(fn(ctx, step, screen) or 0))
            except Exception:
                logger.exception("knowledge plug %s.step_wait failed", mod.__name__)
        return max(0, min(best, int(actions.MAX_WAIT_MS)))
    except Exception:
        logger.exception("knowledge step_wait failed")
        return 0


def post_step(ctx, entry, before, after) -> None:
    """In-memory only: plugs record into ``ctx.kbuf``; fired events are merged
    into the entry. Never touches the DB."""
    try:
        for mod in plugs():
            fn = getattr(mod, "post_step", None)
            if fn is None:
                continue
            try:
                fn(ctx, entry, before, after)
            except Exception:
                logger.exception("knowledge plug %s.post_step failed", mod.__name__)
    except Exception:
        logger.exception("knowledge post_step failed")


def kbuf_add(ctx, event: dict) -> bool:
    """Append *event* to ``ctx.kbuf`` unless it is full (``MAX_KBUF``)."""
    try:
        buf = getattr(ctx, "kbuf", None)
        if not isinstance(buf, list) or len(buf) >= knowledge_limits.MAX_KBUF:
            return False
        buf.append(event)
        return True
    except Exception:
        logger.exception("knowledge: step failed")
        return False


def snapshots(conn, facts) -> dict:
    """Merged ``snapshot(conn, facts)`` of every plug that has one (sync, runs
    inside ``_read_knowledge``'s worker thread)."""
    out: dict = {}
    for mod in plugs():
        fn = getattr(mod, "snapshot", None)
        if fn is None:
            continue
        try:
            part = fn(conn, facts)
            if isinstance(part, dict):
                out.update(part)
        except Exception:
            logger.exception("knowledge plug %s.snapshot failed", mod.__name__)
    return out


def flush_kbuf(ctx, events: list) -> int:
    """Persist *events* in ONE batched call (run in a worker thread by the
    executor). Only plugs exposing ``flush(package, run_id, events)`` get them,
    so with none there is no DB I/O at all. Returns how many plugs took them."""
    taken = 0
    package = str(getattr(ctx, "package", "") or "")
    run_id = str(getattr(ctx, "run_id", "") or "")
    for mod in plugs():
        fn = getattr(mod, "flush", None)
        if fn is None:
            continue
        try:
            fn(package, run_id, events)
            taken += 1
        except Exception:
            logger.exception("knowledge plug %s.flush failed", mod.__name__)
    return taken
