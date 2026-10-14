"""Reply helpers shared by the adb leaves (one copy of each repeated reply)."""

from __future__ import annotations

from tools.mobile.adb_consts_dev import _DEVICE_PATH_RE
from tools.mobile.adb_host import host


def refuse_device_id(serial: object) -> dict:
    """The refusal reply for a device id that failed validation."""
    return {
        "error": "Refusing to use " + repr(str(serial)[:40]) + " as a device id.",
        "content": None,
    }


def rc_failure(what: str, body: dict, rc: int) -> dict:
    """The refusal for an adb command that RAN and exited non-zero (*what* names it)."""
    detail = str(body.get("err") or "").strip() or str(body.get("out") or "").strip()
    return {
        "error": what
        + " failed ("
        + (detail[:200] if detail else "exit " + str(rc))
        + ").",
        "content": None,
    }


def clamp_timeout(timeout_s: float | None) -> float:
    """A caller's wait, never above the default and never below one second."""
    default = host().DEFAULT_TIMEOUT_S
    if timeout_s is None:
        return default
    return max(1.0, min(float(default), float(timeout_s)))


def _valid_device_path(path: object) -> bool:
    text = str(path or "")
    return bool(_DEVICE_PATH_RE.match(text)) and ".." not in text.split("/")


def _capped(text: str, max_bytes: int) -> tuple[str, bool]:
    """*text* cut to at most *max_bytes* UTF-8 bytes, and whether it was cut."""
    raw = text.encode("utf-8", errors="replace")
    limit = max(0, int(max_bytes))
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", errors="replace"), True
