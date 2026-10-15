"""A dump that finishes after a wait cut it is kept for the next read.

A polled wait cuts its dump at the deadline but never cancels it: the dump runs
to its end on the device and the next read joins it, then starts ANOTHER
dump, paying 2.3-3.3 s for a screen the first one already read. Here the
finished result is held, and the next read of the SAME generation takes it
instead (see :mod:`tools.mobile.dump_reuse`: any command that can change the
screen ends the generation, and an old result is then never served). It is
served at most once and only while young, so a read never sees a screen older
than one dump.
"""

from __future__ import annotations

import asyncio
import copy
import time

from tools.mobile import dump_reuse

_KEPT: dict[str, tuple[dict, int, float]] = {}


def keep_when_done(serial: str, under: int, task: asyncio.Future) -> None:
    """Hold *task*'s result when it succeeds, started under generation *under*."""
    task.add_done_callback(lambda done: _keep(str(serial or ""), under, done))


def _keep(key: str, under: int, done: asyncio.Future) -> None:
    if done.cancelled() or done.exception() is not None:
        return
    result = done.result()
    if not isinstance(result, dict) or result.get("error"):
        return
    if under == dump_reuse.token(key):
        _KEPT[key] = (copy.deepcopy(result), under, time.monotonic())


def take(serial: str) -> dict | None:
    """The kept dump when young and nothing actuated since; used at most once."""
    key = str(serial or "")
    kept = _KEPT.pop(key, None)
    if kept is None or kept[1] != dump_reuse.token(key):
        return None
    if time.monotonic() - kept[2] > dump_reuse.REUSE_MAX_AGE_S:
        return None
    return kept[0]


def reset() -> None:
    """Forget every kept dump (tests; a process-global cache)."""
    _KEPT.clear()
