"""Provision ``mitmdump``: locate, or (with ``apply=True``) fetch and extract.

Reuses ``tools.mobile.downloader.download`` for the fetch: HTTPS-only,
hash-verified, every redirect hop checked against
``downloader.ALLOWED_HOST_SUFFIXES``. Nothing here opens a socket of its own.

Never raises: every public function returns ``{"error", "content"}``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from tools.mobile import downloader
from tools.mobile_capture import mitm_extract, mitm_find, mitm_pins, mitm_safe

logger = logging.getLogger("tools.mobile_capture.mitm_provision")

#: Refusal codes. Constants rather than prose, so a caller branches by NAME.
REASON_NO_MITMDUMP = "no_mitmdump"
REASON_NO_MITM_PIN = "no_mitm_pin"
REASON_NO_MITM_BINARY = "no_mitm_binary"


def _known_reply(content: dict) -> dict:
    """The reply for an already-found binary: nothing was downloaded."""
    return {"error": None, "content": {**content, "downloaded": False}}


def _next_call_reply() -> dict:
    """The by-name refusal that names the exact next call."""
    return {
        "error": REASON_NO_MITMDUMP,
        "content": {
            "found": False,
            "path": "",
            "next_call": ("tools.mobile_capture.mitm_provision.provision(apply=True)"),
        },
    }


def _download_reply() -> dict:
    """Fetch with this host's pin, extract, resolve; refuse by name on failure."""
    pin = mitm_pins.pin_for_platform()
    refused = _provision_unpinned_reply(pin)
    if refused is not None:
        return refused
    failed, extracted = _provision_fetch_and_extract(cast("dict", pin))
    if failed is not None:
        return failed
    return _provision_resolved_reply(cast("dict", extracted))


# ``apply=False`` (the default) NEVER downloads: an already-found binary is
# reported, otherwise the reply refuses by name (``REASON_NO_MITMDUMP``)
# and names the exact next call, rather than a bare ``None``.
#
# ``apply=True`` reuses ``downloader.download`` with this host's pin from
# :data:`MITM_PINS`. A host whose platform key is absent from that table
# refuses by name (``REASON_NO_MITM_PIN``) instead of attempting an
# unverifiable fetch. The archive is extracted WHOLE into a per-version
# directory and the binary's absolute path is RESOLVED inside it -- never
# assumed -- and that resolved path is what is recorded and returned.
def provision(apply: bool = False) -> dict:
    """Locate, or (with ``apply=True``) fetch, ``mitmdump``. Never raises."""
    try:
        found = mitm_find.find_mitmdump()
        if found["error"]:
            return found
        if found["content"]["found"]:
            return _known_reply(found["content"])
        if not apply:
            return _next_call_reply()
        return _download_reply()
    except Exception as exc:
        logger.exception("mobile_capture.mitm_provision.provision failed")
        return {"error": str(exc), "content": None}


def _no_pin_detail(key: tuple[str, str]) -> str:
    """The human text for a host whose platform *key* has no pin."""
    return (
        "No qa-agents release pins mitmdump's SHA-256 for "
        "this host (" + key[0] + "/" + key[1] + ") yet, so "
        "it cannot be downloaded and verified. Install "
        "mitmproxy yourself (https://mitmproxy.org/downloads/) "
        "and put `mitmdump` on PATH, or wait for a pinned "
        "release."
    )


def _provision_unpinned_reply(pin: dict | None) -> dict | None:
    """The by-name refusal when this host has no usable pin, else None."""
    if not pin or not pin.get("url") or not downloader.valid_sha256(pin.get("sha256")):
        key = mitm_pins.platform_key()
        return {
            "error": REASON_NO_MITM_PIN,
            "content": {"found": False, "path": "", "detail": _no_pin_detail(key)},
        }
    return None


def _discard(archive: Path) -> None:
    """Remove the downloaded archive; a failure to do so is ignored."""
    try:
        archive.unlink(missing_ok=True)
    except OSError:
        pass


def _provision_fetch_and_extract(pin: dict) -> tuple[dict | None, dict | None]:
    """Download and extract; return (failure reply or None, extraction result)."""
    url = pin["url"]
    dest_dir = mitm_pins._version_dir(mitm_pins.MITM_VERSION)
    archive_dest = mitm_pins.mitm_dir() / ("download" + mitm_safe._archive_suffix(url))
    got = downloader.download(
        downloader.DownloadRequest(
            url, archive_dest, pin["sha256"], payload_bytes=mitm_safe.MAX_ARCHIVE_BYTES
        )
    )
    if got["error"]:
        return {"error": got["error"], "content": None}, None
    extracted = mitm_extract._extract_archive(Path(got["content"]["path"]), dest_dir)
    _discard(Path(got["content"]["path"]))
    if extracted["error"]:
        return {"error": extracted["error"], "content": None}, None
    return None, extracted


def _missing_binary_reply(dest: str) -> dict:
    """The by-name refusal: the archive extracted but held no binary."""
    return {
        "error": REASON_NO_MITM_BINARY,
        "content": {
            "detail": (
                "the archive extracted cleanly but no "
                + mitm_pins._leaf()
                + " was found anywhere under "
                + dest
            ),
        },
    }


def _set_executable(binary: Path) -> None:
    """Mark *binary* executable; a failure is logged, never raised."""
    try:
        binary.chmod(0o755)
    except OSError:
        logger.info(
            "mobile_capture.mitm_provision: could not set the executable bit on %s",
            binary,
        )


def _provision_resolved_reply(extracted: dict) -> dict:
    """Resolve the binary inside the extracted tree and build the reply."""
    dest = extracted["content"]["dest_dir"]
    binary = mitm_find._resolve_binary(Path(dest), mitm_pins._leaf())
    if binary is None:
        return _missing_binary_reply(dest)
    _set_executable(binary)
    return {
        "error": None,
        "content": {
            "path": str(binary),
            "source": "downloaded",
            "found": True,
            "downloaded": True,
        },
    }
