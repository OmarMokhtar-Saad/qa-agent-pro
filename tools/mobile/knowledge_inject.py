"""What the tester's model is told about the app (contract 1.7). ``agents/``
imports ONLY this module, never ``handlers/``.

Two entry points, both sync, no I/O, never raising, ``""`` when nothing applies:

* ``stable_block(run_id)``: built once per run id and cached, so the prompt
  prefix is byte-stable across the run.
* ``screen_rules(run_id, screen)``: per turn, a dict lookup on the cached
  snapshot by screen key, at most ``MAX_TURN_RULES`` short lines.

Content comes from registered sources: ``fn(snap, screen_key) -> [{"id",
"table", "prio", "text"}]``. ``screen_key`` is ``None`` for the stable block (the
global items). Lower ``prio`` sorts first (tester 0, host 1, learned 2); ties
break by id. Modules named in ``INJECT_SOURCES`` register themselves on import.
Everything echoed from storage goes through ``wrap_untrusted``.
"""

from __future__ import annotations

import importlib
import logging
import threading
from collections import OrderedDict
from typing import Any

from tools.mobile import flow_goal_fit, knowledge_snapshot, screen_key
from tools.mobile import knowledge_limits as limits
from tools.mobile.knowledge_inject_block import (  # noqa: F401
    _line,
    _num,
    screen_text,
    sort_key,
    stable_text,
    take,
)
from tools.mobile.knowledge_inject_core import (  # noqa: F401
    _SCREEN_NOTE_KINDS,
    _TRUST_PRIO,
    _mistake_item,
    _row_item,
    _usable,
)
from tools.mobile.knowledge_inject_core import core as _core
from tools.untrusted import wrap_untrusted  # noqa: F401

logger = logging.getLogger(__name__)

#: Modules (under tools.mobile) that call ``register_source`` when imported.
INJECT_SOURCES: tuple = ("knowledge_inject_flows",)

SOURCES: dict = {}
_LOCK = threading.Lock()
STABLE: OrderedDict = OrderedDict()
LOADED = False


def register_source(name: str, fn) -> None:
    SOURCES[str(name)] = fn


def _load_sources() -> None:
    global LOADED
    if LOADED:
        return
    LOADED = True
    for module in INJECT_SOURCES:
        try:
            importlib.import_module("tools.mobile." + module)
        except Exception:
            logger.exception("inject source %s failed to load", module)


def _items(snap: dict, key: Any) -> list:
    _load_sources()
    out: list = []
    seen: set = set()
    for name, fn in list(SOURCES.items()):
        try:
            got = fn(snap, key) or []
        except Exception:
            logger.exception("inject source %s failed", name)
            continue
        out.extend(take(got, seen))
    out.sort(key=sort_key)
    return out


def stable_block(run_id: Any) -> str:
    try:
        rid = str(run_id or "")
        with _LOCK:
            if rid in STABLE:
                return STABLE[rid]
        snap = knowledge_snapshot.for_run(rid) or knowledge_snapshot.load_for_run(rid)
        snap = dict(snap, run_goal=flow_goal_fit.run_goal(rid))
        block = stable_text(_items(snap, None))
        with _LOCK:
            STABLE[rid] = block
            while len(STABLE) > limits.MAX_CACHED_RUNS:
                STABLE.popitem(last=False)
        return block
    except Exception:
        logger.exception("stable_block failed")
        return ""


def _screen_items(snap: dict, screen: Any, key: str) -> list:
    """Items under the screen's key plus those still stored under its old ``sk1`` key."""
    items = _items(snap, key)
    legacy = screen_key.legacy_key(screen)
    if not legacy:
        return items
    have = {str(i.get("id")) for i in items}
    more = [i for i in _items(snap, legacy) if str(i.get("id")) not in have]
    return sorted(items + more, key=sort_key)


def screen_rules(run_id: Any, screen: Any) -> str:
    try:
        snap = knowledge_snapshot.for_run(run_id) or knowledge_snapshot.load_for_run(
            run_id
        )
        sk = screen_key.screen_key(screen)
        if getattr(sk, "degraded", True):
            return ""
        return screen_text(_screen_items(snap, screen, sk.key))
    except Exception:
        logger.exception("screen_rules failed")
        return ""


def forget(run_id: Any) -> None:
    """Drop the cached block (and snapshot) for a finished run."""
    with _LOCK:
        STABLE.pop(str(run_id or ""), None)
    knowledge_snapshot.drop(run_id)


register_source("core", _core)
