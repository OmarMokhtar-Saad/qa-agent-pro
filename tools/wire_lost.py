"""The JSON-RPC error that answers a ``tools/call`` the server never replied to (stdlib only)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from config.settings_guard import never_raise

LOST_CODE = -32001
MAX_DESCRIBE_BYTES = 1_000_000  # larger lines are sized, never parsed
_HEAD_ID = re.compile(rb'"id"\s*:\s*("(?:[^"\\]|\\.)*"|-?\d+)')
LOST_PROVEN = (
    "Request lost, not slow: the launcher forwarded this tools/call but the server "
    "sent no reply and no progress within {secs} s, so it was lost in transit and "
    "did not run. Resend it ONCE; do not retry in a loop."
)
LOST_UNPROVEN = (
    "No reply to this tools/call within {secs} s. It may not have reached the server, "
    "so it may not have run: check the current state before resending, then resend "
    "at most once."
)


def _clip(value: object) -> str:
    return str(value).encode("unicode_escape").decode("ascii")[:80]


def _head_id(line: bytes) -> str:
    """The id of an oversize line from its first bytes only, or ``-``."""
    found = _HEAD_ID.search(line[:1024])
    if found is None:
        return "-"
    with never_raise():
        return str(json.loads(found.group(1)))
    return "-"


def describe(line: bytes) -> tuple[str, str, int]:
    """(method or '-', id or '-', byte size) of one JSON-RPC line."""
    if len(line) > MAX_DESCRIBE_BYTES:
        return "-", _head_id(line), len(line)
    try:
        msg = json.loads(line)
    except ValueError:
        return "-", "-", len(line)
    if not isinstance(msg, dict):
        return "-", "-", len(line)
    return str(msg.get("method", "-")), str(msg.get("id", "-")), len(line)


def _params(line: bytes) -> dict:
    with never_raise():
        params = json.loads(line).get("params")
        return params if isinstance(params, dict) else {}
    return {}


def request_token(line: bytes) -> str | None:
    """params._meta.progressToken of a request, or ``None``."""
    meta = _params(line).get("_meta")
    token = meta.get("progressToken") if isinstance(meta, dict) else None
    return None if token is None else str(token)


def progress_token(line: bytes) -> str | None:
    """params.progressToken of a notifications/progress line, or ``None``."""
    token = _params(line).get("progressToken")
    return None if token is None else str(token)


def raw_id(line: bytes) -> object:
    """The JSON-RPC id of *line* with its original type, or ``None`` when absent."""
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    return msg.get("id") if isinstance(msg, dict) else None


def cancelled_id(line: bytes) -> str:
    """params.requestId of a cancel notification; nothing else is read."""
    params = json.loads(line).get("params")
    if isinstance(params, dict) and "requestId" in params:
        return str(params["requestId"])
    return "-"


def lost_error_line(rid: object, secs: float, proven: bool) -> bytes:
    """One newline-terminated JSON-RPC error line for request *rid*."""
    text = (LOST_PROVEN if proven else LOST_UNPROVEN).format(secs=int(secs))
    error = {"code": LOST_CODE, "message": text}
    body = {"jsonrpc": "2.0", "id": rid, "error": error}
    return json.dumps(body).encode("utf-8") + b"\n"


class NullWire:
    def seen(self, direction: str, line: bytes) -> bool:
        return False

    def set_lost_handler(self, handler: Callable[[bytes], bool]) -> None:
        return None
