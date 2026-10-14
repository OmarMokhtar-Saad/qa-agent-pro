"""Is the installed keyboard the PINNED build? Hash it on the device."""

from __future__ import annotations

import logging

from tools.mobile import adb, downloader
from tools.mobile.ime_consts import _PM_PATH_RE, _SHA256_TOKEN_RE
from tools.mobile.ime_host import host

logger = logging.getLogger("tools.mobile.ime")


def _verdict(pinned: bool | None, detail: str) -> dict:
    """One verify_pinned answer. ``pinned`` None means NOT DETERMINED."""
    return {"error": None, "content": {"pinned": pinned, "detail": detail}}


def _shell_out(result: dict) -> str | None:
    """stdout of a device command that ran and exited 0, else None."""
    body = result.get("content") or {}
    if result.get("error") or body.get("rc") not in (0, None):
        return None
    return str(body.get("out") or "")


async def _installed_path(serial: str, package: str) -> str | dict:
    """The device path of the installed APK, or a verdict that ends the check."""
    found = await adb.shell(
        serial, ["pm", "path", package], timeout=adb.PM_LIST_TIMEOUT_S
    )
    out = _shell_out(found)
    if out is None:
        return _verdict(None, "pm path failed")
    if not out.strip():
        return _verdict(None, "pm path printed nothing")
    lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("package:")]
    if len(lines) > 1:
        return _verdict(False, "installed as a split APK; the pinned build is one file")
    matched = _PM_PATH_RE.fullmatch(lines[0]) if lines else None
    if matched is None:
        return _verdict(None, "pm path answered an unexpected shape")
    return matched.group(1)


async def _installed_digest(serial: str, package: str) -> list[str] | dict:
    """``sha256sum`` tokens of the installed APK, or a verdict that ends the check."""
    located = await _installed_path(serial, package)
    if isinstance(located, dict):
        return located
    hashed = await adb.shell(
        serial, ["sha256sum", located], timeout=adb.PM_LIST_TIMEOUT_S
    )
    return (_shell_out(hashed) or "").split()


# Is the package on the device the PINNED build, not just one with its name?
#
# ``installed`` matches the package name only, so any app installed under the
# keyboard's package id would be enabled and selected and then receive every
# typed value, credentials included. This hashes the installed file on the
# device and compares it with the manifest's sha256 (case-insensitive).
#
# ``{"error": None, "content": {"pinned": True | False | None, "detail"}}``.
# None means could not be determined (the manifest has no usable hash, `pm path`
# failed or was silent or of an unexpected shape, no `sha256sum`, no hex digest)
# and is never read as a match or as a mismatch. A split install is False: the
# pinned build is a single file. With no usable hash this returns None BEFORE
# any adb call. Never raises on a bad answer; adb errors become None.
async def verify_pinned(serial: str) -> dict:
    """Is the package on the device the PINNED build, not just one with its name?"""
    info = host().manifest().get("content") or {}
    want = str(info.get("sha256") or "").strip().lower()
    if not downloader.valid_sha256(want):
        return _verdict(None, "the manifest names no usable hash")
    tokens = await _installed_digest(serial, str(info.get("package") or ""))
    if isinstance(tokens, dict):
        return tokens
    if not tokens or not _SHA256_TOKEN_RE.fullmatch(tokens[0]):
        return _verdict(None, "sha256sum gave no digest")
    if tokens[0].lower() == want:
        return _verdict(True, "the installed build matches the pinned hash")
    return _verdict(False, "the installed build differs from the pinned hash")


async def is_foreign_build(serial: str) -> bool:
    """True ONLY when the device proved the installed build is not the pinned one.

    Unverifiable is logged and answers False: a device without `sha256sum` must
    not be reinstalled on every run, and the pinned APK is still what the host
    downloads and verifies.
    """
    verdict = (await host().verify_pinned(serial)).get("content") or {}
    if verdict.get("pinned") is None:
        logger.warning(
            "mobile.ime: could not verify the installed keyboard against the "
            "pinned build (%s)",
            verdict.get("detail"),
        )
    return verdict.get("pinned") is False
