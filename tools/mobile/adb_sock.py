"""A pure-asyncio client for the adb SERVER protocol (127.0.0.1:5037).

One TCP connection per command (the protocol serves one service per
connection): ``host:transport:<serial>`` then ``shell,v2,raw:<cmd>`` (frames:
1-byte id, 4-byte little-endian length; 0 stdin, 1 stdout, 2 stderr, 3 exit)
or ``exec:<cmd>`` (a raw binary stream, no exit code). No fork/exec per call.

Any socket or protocol fault returns ``None`` so ``adb_run`` takes the
subprocess path for that call; the client then rests for ``RETRY_S``.
Kill-switch: ``ADB_SOCKET_DISABLED=1`` (default off) forces subprocess.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, replace

from tools.prun_types import RunResult

RETRY_S = 5.0
_DEFAULT_PORT = 5037
_VERBS = {"shell": "shell,v2,raw:", "exec-out": "exec:"}
_STDOUT, _STDERR, _EXIT = 1, 2, 3
_CLOSE_STDIN = bytes([4]) + (0).to_bytes(4, "little")  # kIdCloseStdin
_CHUNK = 32768
_UNHEALTHY_UNTIL = [0.0]


class AdbSockError(Exception):
    """The server answered something this client does not understand or refused."""


@dataclass(frozen=True)
class Request:
    serial: str | None
    verb: str
    command: str
    stdin: bytes | None = None


def _now() -> float:
    return time.monotonic()


def parse(cmd: list[str], bare: bool = False) -> Request | None:
    """``adb [-s S] shell|exec-out <args>`` as a request; anything else is None.

    *bare* admits ``adb [-s S] shell`` alone (a script fed on stdin).
    """
    rest = cmd[1:]
    serial = None
    if rest[:1] == ["-s"] and len(rest) > 1:
        serial, rest = rest[1], rest[2:]
    if len(rest) < (1 if bare else 2) or rest[0] not in _VERBS:
        return None
    if len(rest) == 1 and rest[0] != "shell":
        return None
    return Request(serial, rest[0], " ".join(rest[1:]))


def enabled() -> bool:
    off = os.environ.get("ADB_SOCKET_DISABLED", "").strip().lower()
    return off in ("", "0", "false", "no") and _now() >= _UNHEALTHY_UNTIL[0]


def applicable(cmd: list[str], stdin: bytes | None = None) -> bool:
    return enabled() and parse(cmd, stdin is not None) is not None


def mark_unhealthy() -> None:
    _UNHEALTHY_UNTIL[0] = _now() + RETRY_S


def reset() -> None:
    _UNHEALTHY_UNTIL[0] = 0.0


def server_address() -> tuple[str, int]:
    """Host and port from ADB_SERVER_SOCKET (``tcp:host:port``) or the port var."""
    spec = os.environ.get("ADB_SERVER_SOCKET", "").strip()
    port = int(os.environ.get("ANDROID_ADB_SERVER_PORT") or _DEFAULT_PORT)
    if not spec:
        return "127.0.0.1", port
    host, _, tail = spec.removeprefix("tcp:").rpartition(":")
    if spec.startswith(("local", "acceptfd")) or not tail.isdigit():
        raise AdbSockError("unsupported ADB_SERVER_SOCKET")
    return host or "127.0.0.1", int(tail)


async def _send(w: asyncio.StreamWriter, r: asyncio.StreamReader, text: str) -> None:
    data = text.encode()
    w.write(b"%04x" % len(data) + data)
    await w.drain()
    status = await r.readexactly(4)
    if status == b"FAIL":
        size = int(await r.readexactly(4), 16)
        reason = (await r.readexactly(size)).decode(errors="replace")
        raise AdbSockError(f"FAIL {reason}")
    if status != b"OKAY":
        raise AdbSockError(f"unexpected status {status!r}")


async def _frames(r: asyncio.StreamReader) -> tuple[int, bytes, bytes]:
    """Read shell-v2 frames up to the exit frame: ``(rc, stdout, stderr)``."""
    out, err = bytearray(), bytearray()
    while True:
        head = await r.readexactly(5)
        body = await r.readexactly(int.from_bytes(head[1:], "little"))
        if head[0] == _EXIT:
            return (body[0] if body else 0), bytes(out), bytes(err)
        if head[0] == _STDOUT:
            out += body
        elif head[0] == _STDERR:
            err += body


async def _converse(
    w: asyncio.StreamWriter, r: asyncio.StreamReader, req: Request
) -> tuple[int, bytes, bytes]:
    to = f"host:transport:{req.serial}" if req.serial else "host:transport-any"
    await _send(w, r, to)
    await _send(w, r, _VERBS[req.verb] + req.command)
    if req.verb == "exec-out":
        return 0, await r.read(-1), b""
    data = req.stdin or b""
    for i in range(0, len(data), _CHUNK):
        part = data[i : i + _CHUNK]
        w.write(bytes([0]) + len(part).to_bytes(4, "little") + part)
    w.write(_CLOSE_STDIN)  # EOF so `cat`-style commands finish
    await w.drain()
    return await _frames(r)


async def _talk(req: Request) -> tuple[int, bytes, bytes]:
    host, port = server_address()
    reader, writer = await asyncio.open_connection(host, port)
    try:
        return await _converse(writer, reader, req)
    finally:
        writer.close()


async def try_run(
    cmd: list[str], timeout: float, stdin: bytes | None = None
) -> RunResult | None:
    """Run *cmd* over the server socket; ``None`` means use the subprocess path."""
    req = parse(cmd, stdin is not None) if enabled() else None
    if req is None or (stdin is not None and req.verb != "shell"):
        return None
    if stdin is not None:
        req = replace(req, stdin=stdin)
    try:
        rc, out, err = await asyncio.wait_for(_talk(req), timeout)
    except asyncio.TimeoutError as exc:
        return RunResult(None, timed_out=True, exc=exc)
    except (OSError, EOFError, ValueError, AdbSockError):
        mark_unhealthy()
        return None
    return RunResult(rc, out, err)
