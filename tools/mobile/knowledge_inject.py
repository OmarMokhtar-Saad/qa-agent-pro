"""What the tester's model is told about the app (contract 1.7). ``agents/``
imports ONLY this module, never ``tools/mcp_handlers.py``.

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

from tools.mobile import knowledge_limits as limits
from tools.mobile import knowledge_snapshot, screen_key
from tools.untrusted import wrap_untrusted

logger = logging.getLogger(__name__)

#: Modules (under tools.mobile) that call ``register_source`` when imported.
INJECT_SOURCES: tuple = ()

_SOURCES: dict = {}
_LOCK = threading.Lock()
_STABLE: OrderedDict = OrderedDict()
_LOADED = False


def register_source(name: str, fn) -> None:
    _SOURCES[str(name)] = fn


def _load_sources() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    for module in INJECT_SOURCES:
        try:
            importlib.import_module("tools.mobile." + module)
        except Exception:
            logger.exception("inject source %s failed to load", module)


def _items(snap: dict, key: object) -> list:
    _load_sources()
    out: list = []
    seen: set = set()
    for name, fn in list(_SOURCES.items()):
        try:
            got = fn(snap, key) or []
        except Exception:
            logger.exception("inject source %s failed", name)
            continue
        for item in got:
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            ident = str(item.get("id") or "")
            if ident in seen:
                continue
            seen.add(ident)
            out.append(item)
    out.sort(
        key=lambda i: (
            _num(i.get("prio")),
            -_num(i.get("net")) if i.get("net") is not None else 0.0,
            str(i.get("table") or ""),
            _num(i.get("num")) if i.get("num") is not None else 0.0,
            str(i.get("id") or ""),
        )
    )
    return out


def _num(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return 99.0


def _line(text: object, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap]


def stable_block(run_id: object) -> str:
    try:
        rid = str(run_id or "")
        with _LOCK:
            if rid in _STABLE:
                return _STABLE[rid]
        snap = knowledge_snapshot.for_run(rid) or knowledge_snapshot.load_for_run(rid)
        lines: list = []
        budget = limits.STABLE_BLOCK_TOKENS * 4
        for item in _items(snap, None):
            line = "- " + _line(item.get("text"), limits.STABLE_BLOCK_CHARS)
            if budget - len(line) < 0:
                break
            budget -= len(line) + 1
            lines.append(line)
        block = (
            wrap_untrusted(
                "app_knowledge", "\n".join(lines), limit=limits.STABLE_BLOCK_CHARS
            )
            if lines
            else ""
        )
        with _LOCK:
            _STABLE[rid] = block
            while len(_STABLE) > limits.MAX_CACHED_RUNS:
                _STABLE.popitem(last=False)
        return block
    except Exception:
        logger.exception("stable_block failed")
        return ""


def screen_rules(run_id: object, screen: object) -> str:
    try:
        snap = knowledge_snapshot.for_run(run_id) or knowledge_snapshot.load_for_run(
            run_id
        )
        sk = screen_key.screen_key(screen)
        if getattr(sk, "degraded", True):
            return ""
        lines = [
            "- " + _line(item.get("text"), limits.RULE_CHARS)
            for item in _items(snap, sk.key)[: limits.MAX_TURN_RULES]
        ]
        if not lines:
            return ""
        return wrap_untrusted(
            "app_knowledge_screen",
            "\n".join(lines),
            limit=limits.MAX_TURN_RULES * (limits.RULE_CHARS + 4),
        )
    except Exception:
        logger.exception("screen_rules failed")
        return ""


def forget(run_id: object) -> None:
    """Drop the cached block (and snapshot) for a finished run."""
    with _LOCK:
        _STABLE.pop(str(run_id or ""), None)
    knowledge_snapshot.drop(run_id)


# --- the core source: notes, lessons and mistakes from the snapshot ---------

_TRUST_PRIO = {"tester": 0, "host": 1}
_SCREEN_NOTE_KINDS = ("fact", "precondition")


def _usable(row: dict) -> bool:
    """Only active rows (a note may be needs_recheck); a disputed note is never here."""
    if row.get("proposal_json") or row.get("invalid_at") is not None:
        return False
    table = row.get("_table")
    if table == "notes":
        return row.get("status") in ("active", "needs_recheck")
    return row.get("status") == "active"


def _row_item(row: dict) -> dict:
    table = str(row.get("_table") or "lessons")
    rid = row.get("id")
    net = int(row.get("confirmed") or 0) - int(row.get("contradicted") or 0)
    return {
        "id": "%s:%s" % (table, rid),
        "table": table,
        "num": rid,
        "net": net,
        "prio": _TRUST_PRIO.get(str(row.get("trust") or ""), 2),
        "text": row.get("text"),
    }


def _mistake_item(row: dict) -> dict | None:
    if row.get("status") not in (None, "active") or row.get("invalid_at") is not None:
        return None
    bad = _line(row.get("bad_pattern"), limits.RULE_CHARS)
    if not bad:
        return None
    fix = _line(row.get("fix"), limits.RULE_CHARS)
    text = "Don't %s; %s" % (bad, fix) if fix else "Don't %s" % bad
    return {
        "id": "mistakes:%s" % row.get("id"),
        "table": "mistakes",
        "num": row.get("id"),
        "net": 0,
        "prio": 2,
        "text": text,
    }


def _core(snap: dict, key: object) -> list:
    out: list = []
    if key is None:
        rows = [
            r for r in snap.get("global") or [] if isinstance(r, dict) and _usable(r)
        ]
        return [_row_item(r) for r in rows]
    for row in (snap.get("lessons") or {}).get(key) or []:
        if not isinstance(row, dict) or not _usable(row):
            continue
        if row.get("_table") == "notes" and row.get("kind") not in _SCREEN_NOTE_KINDS:
            continue
        out.append(_row_item(row))
    for row in (snap.get("mistakes") or {}).get(key) or []:
        item = _mistake_item(row) if isinstance(row, dict) else None
        if item is not None:
            out.append(item)
    return out


register_source("core", _core)
