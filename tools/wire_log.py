"""Wire log: one line per JSON-RPC message (direction, method, id, size).

Never params or results, so no secret reaches the file. Lets a lost MCP call
be located: compare launcher-in, server-in and the client's own dispatch
times. A request id with no reply in NO_REPLY_S is logged as "no reply".
stdlib only; every failure degrades to a no-op (logging never blocks a call).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config.settings_guard import never_raise
from tools.log_setup_sweep import _pid_is_alive

NO_REPLY_S = 110.0  # just under the 120 s host timeout
KEEP_DAYS = 3
MAX_DESCRIBE_BYTES = 1_000_000  # larger lines are sized, never parsed


def describe(line: bytes) -> tuple[str, str, int]:
    """(method or '-', id or '-', byte size) of one JSON-RPC line."""
    if len(line) > MAX_DESCRIBE_BYTES:
        return "-", "-", len(line)
    try:
        msg = json.loads(line)
    except ValueError:
        return "-", "-", len(line)
    if not isinstance(msg, dict):
        return "-", "-", len(line)
    return str(msg.get("method", "-")), str(msg.get("id", "-")), len(line)


def _clip(value: object) -> str:
    return str(value).encode("unicode_escape").decode("ascii")[:80]


def _cancelled_id(line: bytes) -> str:
    """params.requestId of a cancel notification; nothing else is read."""
    params = json.loads(line).get("params")
    if isinstance(params, dict) and "requestId" in params:
        return str(params["requestId"])
    return "-"


class WireLog:
    def __init__(self, logger: logging.Logger, track_replies: bool) -> None:
        self._log = logger
        self._track = track_replies
        self._timers: dict[str, threading.Timer] = {}
        self._late: set[str] = set()
        self._lock = threading.Lock()

    def seen(self, direction: str, line: bytes) -> None:
        with never_raise():
            self._seen(direction, line)

    def _seen(self, direction: str, line: bytes) -> None:
        method, rid, size = describe(line)
        self._log.info(
            "%s method=%s id=%s bytes=%d", direction, _clip(method), _clip(rid), size
        )
        if not self._track or direction not in ("in", "out"):
            return
        if direction == "in" and method == "notifications/cancelled":
            self._disarm(_cancelled_id(line))
        elif rid != "-" and direction == "in" and method != "-":
            self._arm(rid, method)
        elif rid != "-" and direction == "out" and method == "-":
            self._disarm(rid)

    def _arm(self, rid: str, method: str) -> None:
        timer = threading.Timer(NO_REPLY_S, self._expired, (rid, method))
        timer.daemon = True
        with self._lock:
            old = self._timers.pop(rid, None)
            self._timers[rid] = timer
        if old is not None:
            old.cancel()
        timer.start()

    def _disarm(self, rid: str) -> None:
        with self._lock:
            timer = self._timers.pop(rid, None)
            late = rid in self._late
            self._late.discard(rid)
        if timer is not None:
            timer.cancel()
        if late:
            self._log.info("late reply id=%s", _clip(rid))

    def _expired(self, rid: str, method: str) -> None:
        with self._lock:
            self._timers.pop(rid, None)
            if len(self._late) < 1000:
                self._late.add(rid)
        self._log.warning("no reply for id=%s method=%s", _clip(rid), _clip(method))


class NullWire:
    def seen(self, direction: str, line: bytes) -> None:
        return None


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
