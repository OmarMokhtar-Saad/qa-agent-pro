"""Wire log: one line per JSON-RPC message (direction, method, id, size).

Never params or results, so no secret reaches the file. Lets a lost MCP call
be located: compare launcher-in, server-in and the client's own dispatch
times. A tools/call id with no reply in NO_REPLY_S is logged as "no reply"
and, when a lost-handler is set, answered with an error (see wire_lost).
stdlib only; every failure degrades to a no-op (logging never blocks a call).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config.settings_guard import never_raise
from tools.log_setup_sweep import _pid_is_alive
from tools.wire_lost import (
    NullWire,
    _clip,
    cancelled_id,
    describe,
    lost_error_line,
    progress_token,
    raw_id,
    request_token,
)

NO_REPLY_S = 110.0  # just under the 120 s host timeout
KEEP_DAYS = 3


class WireLog:
    def __init__(self, logger: logging.Logger, track_replies: bool) -> None:
        self._log = logger
        self._track = track_replies
        self._timers: dict[str, threading.Timer] = {}
        self._late: set[str] = set()
        self._answered: set[str] = set()
        self._raw: dict[str, object] = {}
        self._tok: dict[str, str | None] = {}
        self._on_lost: Callable[[bytes], bool] | None = None
        self._lock = threading.Lock()

    def set_lost_handler(self, handler: Callable[[bytes], bool]) -> None:
        """Send an error line to the host for a lost tools/call; True when written."""
        self._on_lost = handler

    def seen(self, direction: str, line: bytes) -> bool:
        """Log one line; True when an OUT line answers an id already failed as lost."""
        with never_raise():
            return self._seen(direction, line)
        return False

    def _seen(self, direction: str, line: bytes) -> bool:
        method, rid, size = describe(line)
        self._log.info(
            "%s method=%s id=%s bytes=%d", direction, _clip(method), _clip(rid), size
        )
        if not self._track or direction not in ("in", "out"):
            return False
        if direction == "in" and method == "notifications/cancelled":
            self._disarm(cancelled_id(line))
        elif rid != "-" and direction == "in" and method != "-":
            if method == "tools/call":
                self._raw[rid] = raw_id(line)
                self._tok[rid] = request_token(line)
            self._arm(rid, method)
        elif rid != "-" and direction == "out" and method == "-":
            return self._disarm(rid)
        elif direction == "out" and method == "notifications/progress":
            self._progress(progress_token(line))
        return False

    def _progress(self, token: str | None) -> None:
        """Progress for a live token proves receipt: restart that call's watchdog."""
        with self._lock:
            for rid, tok in list(self._tok.items()):
                if tok is not None and tok == token:
                    self._arm_locked(rid, "tools/call")

    def _arm(self, rid: str, method: str) -> None:
        with self._lock:
            self._arm_locked(rid, method)

    def _arm_locked(self, rid: str, method: str) -> None:
        timer = threading.Timer(NO_REPLY_S, self._expired, (rid, method))
        timer.daemon = True
        old = self._timers.pop(rid, None)
        self._timers[rid] = timer
        if old is not None:
            old.cancel()
        timer.start()

    def _disarm(self, rid: str) -> bool:
        with self._lock:
            timer = self._timers.pop(rid, None)
            self._raw.pop(rid, None)
            self._tok.pop(rid, None)
            late = rid in self._late
            answered = rid in self._answered
            self._late.discard(rid)
            self._answered.discard(rid)
        if timer is not None:
            timer.cancel()
        if late:
            what = "lost-error sent, dropped" if answered else "no-reply logged"
            self._log.info("reply after %s id=%s", what, _clip(rid))
        return answered

    def _expired(self, rid: str, method: str) -> None:
        with self._lock:
            if self._timers.get(rid) is not threading.current_thread():
                return  # replaced or disarmed while this timer was firing
            del self._timers[rid]
            raw = self._raw.pop(rid, None)
            proven = self._tok.pop(rid, None) is not None
            if len(self._late) < 1000:
                self._late.add(rid)
            lost = method == "tools/call" and raw is not None
            if lost and self._on_lost is not None:
                self._answered.add(rid)  # same section: no gap for a racing reply
        self._log.warning("no reply for id=%s method=%s", _clip(rid), _clip(method))
        if lost:
            self._answer_lost(rid, raw, proven)

    def _answer_lost(self, rid: str, raw: object, proven: bool) -> None:
        handler = self._on_lost
        if handler is None:
            return
        sent = False
        with never_raise():
            sent = bool(handler(lost_error_line(raw, NO_REPLY_S, proven)))
        if sent:
            self._log.warning("answered lost id=%s: host told to resend", _clip(rid))
            return
        with self._lock:
            self._answered.discard(rid)


def _pid_alive(path: Path) -> bool:
    """True when the pid in ``wire-<name>-<pid>.log[.N]`` is a live process."""
    try:
        pid = int(path.name.split(".")[0].rsplit("-", 1)[1])
    except (ValueError, IndexError):
        return False
    return _pid_is_alive(pid)


def _sweep(log_dir: Path) -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    for old in log_dir.glob("wire-*.log*"):
        try:
            if old.stat().st_mtime < cutoff and not _pid_alive(old):
                old.unlink()
        except OSError:
            continue


def open_wire_log(
    name: str, base: Path, track_replies: bool = False
) -> WireLog | NullWire:
    """Rotating ``data/logs/wire-<name>-<pid>.log`` under ``base``; never raises."""
    wire: WireLog | None = None
    with never_raise():
        wire = _open(name, base, track_replies)
    if wire is None:
        _note_off(base)
        return NullWire()
    return wire


def _note_off(base: Path) -> None:
    with never_raise():
        path = base / "data" / "logs" / f"wire-off-{os.getpid()}.log"
        path.write_text("wire log off: open failed\n", encoding="utf-8")


def _open(name: str, base: Path, track_replies: bool) -> WireLog:
    log_dir = base / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _sweep(log_dir)
    logger = logging.getLogger(f"qa_agents.wire.{name}.{os.getpid()}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(
        log_dir / f"wire-{name}-{os.getpid()}.log",
        maxBytes=1_000_000,
        backupCount=1,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return WireLog(logger, track_replies)
