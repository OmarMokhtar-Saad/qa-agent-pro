"""The preflight-friendly view of the pinned-keyboard manifest."""

from __future__ import annotations

from tools.mobile.ime_consts import NOT_PINNED, NOT_PINNED_FIX
from tools.mobile.ime_host import host


def _pinned_view(content: dict) -> dict:
    return {
        "error": None,
        "content": {
            "ok": True,
            "reason": "",
            "detail": "pinned: "
            + str(content.get("ime_id"))
            + " @ "
            + str(content.get("version") or "unversioned"),
            "fix": "",
        },
    }


def _unpinned_view(resolved: dict) -> dict:
    return {
        "error": None,
        "content": {
            "ok": False,
            "reason": NOT_PINNED,
            "detail": str(resolved["error"]),
            "fix": NOT_PINNED_FIX,
        },
    }


def manifest_status() -> dict:
    """``{ok, reason, detail, fix}`` -- the preflight-friendly view of the pin."""
    resolved = host().manifest()
    if not resolved.get("error"):
        return _pinned_view(resolved["content"] or {})
    return _unpinned_view(resolved)
