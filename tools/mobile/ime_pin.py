"""The pinned QA input method identity: manifest resolution and SHA-256 check.

A leaf of the mobile package: it imports nothing from it, so ``downloader``
and ``ime`` can both depend on it without forming a cycle. ``ime`` re-exports
:func:`manifest`, ``MANIFEST_MODULE`` and ``NOT_PINNED_DETAIL``; ``downloader``
re-exports :func:`valid_sha256`.
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

_HEX = frozenset("0123456789abcdef")

#: The module that pins the published qa-ime APK. Imported by NAME, at call time.
MANIFEST_MODULE = "tools.mobile.ime_manifest"

NOT_PINNED_DETAIL = (
    "The QA input method is not pinned yet on this install: `"
    + MANIFEST_MODULE.replace(".", "/")
    + ".py` is missing or carries no release asset URL and SHA-256, so the "
    "APK cannot be fetched or verified. Nothing was downloaded."
)


def valid_sha256(value: object) -> bool:
    """True for exactly 64 lower-case-able hex characters."""
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(char in _HEX for char in text)


def _manifest_actions(module: object, package: str) -> dict:
    """The broadcast action names, each defaulting to ``<package>.<NAME>``."""
    return {
        "input": str(getattr(module, "ACTION_INPUT", package + ".INPUT")),
        "clear": str(getattr(module, "ACTION_CLEAR", package + ".CLEAR")),
        "query": str(getattr(module, "ACTION_QUERY", package + ".QUERY")),
        # Batch 4b: only the new APK answers these; 1.0.0 ignores them.
        "arm": str(getattr(module, "ACTION_ARM", package + ".ARM")),
        "disarm": str(getattr(module, "ACTION_DISARM", package + ".DISARM")),
        "dump": str(getattr(module, "ACTION_DUMP", package + ".DUMP")),
    }


_PIN_FIELDS = {
    "package": "PACKAGE",
    "service": "SERVICE",
    "url": "ASSET_URL",
    "sha256": "SHA256",
}


def _attr(module: object, name: str) -> str:
    return str(getattr(module, name, "") or "")


def _load_module() -> tuple[object | None, dict | None]:
    """``(module, None)`` or ``(None, refusal)``."""
    try:
        return importlib.import_module(MANIFEST_MODULE), None
    except ImportError:
        return None, {"error": NOT_PINNED_DETAIL, "content": None}
    except Exception as exc:  # pragma: no cover - a broken manifest module
        logger.exception("mobile.ime: %s could not be imported", MANIFEST_MODULE)
        return None, {"error": str(exc), "content": None}


def _nameless() -> dict:
    return {
        "error": (
            MANIFEST_MODULE
            + " exists but names no IME package/service, so nothing can be "
            "installed or selected."
        ),
        "content": None,
    }


def _content(module: object, pin: dict) -> dict:
    return {
        "error": None,
        "content": {
            "version": _attr(module, "IME_VERSION"),
            "package": pin["package"],
            "service": pin["service"],
            "ime_id": pin["package"] + "/" + pin["service"],
            "url": pin["url"],
            "sha256": pin["sha256"],
            "actions": _manifest_actions(module, pin["package"]),
        },
    }


def manifest() -> dict:
    """The pinned IME identity ``{"error", "content": {...}}``, or a refusal."""
    module, refusal = _load_module()
    if refusal is not None:
        return refusal
    pin = {k: _attr(module, "IME_" + v) for k, v in _PIN_FIELDS.items()}
    pin["sha256"] = pin["sha256"].strip().lower()
    if not pin["package"] or not pin["service"]:
        return _nameless()
    if not valid_sha256(pin["sha256"]) or not pin["url"].startswith("https://"):
        return {"error": NOT_PINNED_DETAIL, "content": None}
    return _content(module, pin)
