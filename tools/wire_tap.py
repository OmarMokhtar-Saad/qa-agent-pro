"""Tee the server's stdin into the wire log, line by line, before dispatch."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

from config.settings_guard import never_raise
from tools.wire_log import NullWire, WireLog, open_wire_log


class _TapRaw(io.RawIOBase):
    def __init__(self, real: Any, wire: WireLog | NullWire) -> None:
        self._real = real
        self._wire = wire
        self._partial = bytearray()

    def readable(self) -> bool:
        return True

    def readinto(self, buf: Any) -> int:
        data = self._real.read1(len(buf))
        buf[: len(data)] = data
        self._feed(data)
        if not data and self._partial:  # EOF: log a last line with no "\n"
            self._wire.seen("in", bytes(self._partial))
            self._partial.clear()
        return len(data)

    def _feed(self, data: bytes) -> None:
        start = len(self._partial)  # only the new bytes can hold a new newline
        self._partial.extend(data)
        end = 0
        pos = self._partial.find(b"\n", start)
        while pos != -1:
            self._wire.seen("in", bytes(self._partial[end : pos + 1]))
            end = pos + 1
            pos = self._partial.find(b"\n", end)
        del self._partial[:end]


def install_stdin_tap(base: Path) -> None:
    """Replace sys.stdin so every request line is logged as it is read."""
    with never_raise():
        wire = open_wire_log("server", base)
        raw = _TapRaw(sys.stdin.buffer, wire)
        sys.stdin = io.TextIOWrapper(io.BufferedReader(raw), encoding="utf-8")
