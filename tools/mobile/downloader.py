"""HTTPS-only, hash-verified, resumable downloads for the mobile lane.

Three refusals matter more than the happy path, and all three name numbers:

* **no pinned hash** -- a download nobody can verify is not attempted at all;
* **hash mismatch** -- the expected and actual digests and the byte count are
  all in the message, and the partial file is deleted;
* **not enough disk** -- free, required and the shortfall are all in the
  message, and the check runs BEFORE the first byte is requested.

Stdlib only (``urllib``). Nothing here retries indefinitely and nothing here
raises to a caller: every path returns ``{"error", "content"}``.
"""

from __future__ import annotations

import hashlib  # noqa: F401  (patch target)
import json  # noqa: F401  (patch target)
import logging
import os  # noqa: F401  (patch target)
import sys
import time  # noqa: F401  (patch target)
import urllib.error
import urllib.request  # noqa: F401  (patch target)
from pathlib import Path

from tools import settings_port
from tools.mobile import downloader_host, ime_pin, paths  # noqa: F401
from tools.mobile.downloader_args import DownloadRequest, FetchJob  # noqa: F401
from tools.mobile.downloader_disk import (  # noqa: F401
    CHUNK,
    _disk_refusal,
    _gb,
    check_disk,
    digest_file,
    required_bytes,
    write_progress,
)
from tools.mobile.downloader_fetch import (  # noqa: F401
    _PROGRESS_INTERVAL_S,
    _delivered,
    _fetch_and_install,
    _fetch_part,
    _file_name,
    _hash_mismatch,
    _progress_base,
    _stream_to_part,
    fetch_job,
)
from tools.mobile.downloader_net import (  # noqa: F401
    ALLOWED_HOST_SUFFIXES,
    RedirectRefused,
    _GuardedRedirects,
    _is_pinned_ime,
    _open_resuming,
    host_allowed,
    open_url,
    url_refusal,
)
from tools.mobile.ime_pin import _HEX, valid_sha256  # noqa: F401

logger = logging.getLogger(__name__)

#: Socket timeout for a single read. The whole transfer is not time-capped:
#: a 1GB system image on a slow link is legitimately slow, and killing it at an
#: arbitrary wall-clock bound is how a resumable download becomes a loop.
SOCKET_TIMEOUT_S = 60

#: The archive is unpacked NEXT to itself, so "room for the file" is never room
#: enough. Required space is the larger of factor*payload and payload+floor.
DISK_HEADROOM_FACTOR = 2.5
DISK_HEADROOM_FLOOR = 256 * 1024 * 1024


def _download_refusal(url: str, sha256: str) -> str:
    """The reason *url* may not be fetched at all, or ``""`` when it may."""
    on = settings_port.get().qa_mobile_https_capture_enabled
    if not on and not _is_pinned_ime(url, sha256):
        return (
            "Refusing to download: HTTPS capture needs "
            "`QA_MOBILE_HTTPS_CAPTURE_ENABLED=true` in `.env` and an "
            "MCP server restart. Nothing was fetched."
        )
    return url_refusal(url, sha256)


def _early(req: DownloadRequest, dest_path: Path, want: str) -> dict | None:
    """The reply that needs no request: already cached, or no room on disk."""
    if dest_path.is_file() and digest_file(dest_path) == want:
        return _delivered(dest_path, cached=True)
    disk_refusal = _disk_refusal(req.payload_bytes, dest_path)
    if disk_refusal:
        return {"error": disk_refusal, "content": None}
    return None


def _run(req: DownloadRequest) -> dict:
    """Refuse, answer from cache, or fetch: the whole download in order."""
    refusal = _download_refusal(req.url, req.sha256)
    if refusal:
        return {"error": refusal, "content": None}
    want = str(req.sha256).strip().lower()
    dest_path = Path(req.dest)
    early = _early(req, dest_path, want)
    if early is not None:
        return early
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    return _fetch_and_install(fetch_job(req, dest_path), dest_path, want)


# Fetch *url* to *dest*, verifying SHA-256. Resumable, never raises.
#
# Returns ``{"error", "content": {"path", "bytes", "cached", "verified"}}``.
# A *dest* that already exists and already hashes correctly is returned
# immediately with ``cached=True`` and no request is made -- which is also
# what makes a second run issue no downloads.
#
# THE KILL-SWITCH LIVES HERE, at the innermost public function that fetches:
# a guard on a caller is only as good as the list of callers, and that list
# grew by one in the very commit meant to close that class.
def download(req: DownloadRequest) -> dict:
    try:
        return _run(req)
    except RedirectRefused as exc:
        # Ahead of the ValueError clause below on purpose: a refused hop is a
        # decision this module made, and the tester must read it as a refusal
        # rather than as "the download failed".
        logger.warning("mobile.downloader: refused a redirect: %s", exc)
        return {"error": str(exc), "content": None}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("mobile.downloader: %s failed: %s", str(req.url)[:120], exc)
        return {
            "error": "Download of " + str(req.url)[:200] + " failed: " + str(exc),
            "content": None,
        }
    except Exception as exc:
        logger.exception("mobile.downloader.download failed")
        return {"error": str(exc), "content": None}


downloader_host.bind(sys.modules[__name__])
