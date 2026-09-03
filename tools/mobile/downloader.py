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

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from config.settings import settings
from tools.mobile import paths

logger = logging.getLogger(__name__)

#: Read size. Also the digest block size.
CHUNK = 256 * 1024

#: Socket timeout for a single read. The whole transfer is not time-capped:
#: a 1GB system image on a slow link is legitimately slow, and killing it at an
#: arbitrary wall-clock bound is how a resumable download becomes a loop.
SOCKET_TIMEOUT_S = 60

#: The archive is unpacked NEXT to itself, so "room for the file" is never room
#: enough. Required space is the larger of factor*payload and payload+floor.
DISK_HEADROOM_FACTOR = 2.5
DISK_HEADROOM_FLOOR = 256 * 1024 * 1024

_HEX = frozenset("0123456789abcdef")

#: Progress files are written no more often than this.
_PROGRESS_INTERVAL_S = 1.0


def _gb(value: float) -> str:
    return "%.2f GB" % (float(value) / (1024.0**3))


def required_bytes(payload_bytes: int) -> int:
    """Disk space demanded for a *payload_bytes* download, headroom included."""
    payload = max(0, int(payload_bytes))
    if payload == 0:
        return 0
    return int(max(payload * DISK_HEADROOM_FACTOR, payload + DISK_HEADROOM_FLOOR))


def check_disk(payload_bytes: int, target: Path | None = None) -> dict:
    """``{ok, detail, free, need}`` for a planned download.

    An undeterminable free-space reading is reported as NOT ok: "unknown" must
    never read as "plenty", because the failure it hides is a half-written SDK.
    """
    try:
        need = required_bytes(payload_bytes)
        free = paths.free_bytes(target)
        if free < 0:
            return {
                "error": None,
                "content": {
                    "ok": False,
                    "detail": (
                        "Could not determine free space on the volume holding "
                        "the mobile cache, so a "
                        + _gb(need)
                        + " download is not started."
                    ),
                    "free": free,
                    "need": need,
                },
            }
        ok = free >= need
        detail = (
            _gb(free)
            + " free, "
            + _gb(need)
            + " required (payload "
            + _gb(payload_bytes)
            + " plus unpack headroom)"
        )
        if not ok:
            detail = (
                "Not enough disk space: "
                + detail
                + ". Free at least "
                + _gb(need - free)
                + " and try again."
            )
        return {
            "error": None,
            "content": {"ok": ok, "detail": detail, "free": free, "need": need},
        }
    except Exception as exc:
        logger.exception("mobile.downloader.check_disk failed")
        return {"error": str(exc), "content": None}


def digest_file(path: Path) -> str:
    """Lower-case SHA-256 hex of *path*, read in chunks."""
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            sha.update(block)
    return sha.hexdigest()


def valid_sha256(value: object) -> bool:
    """True for exactly 64 lower-case-able hex characters."""
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(char in _HEX for char in text)


def write_progress(progress_path: str | Path | None, payload: dict) -> None:
    """Atomically write a progress JSON file. Failure is logged, never raised."""
    if not progress_path:
        return
    try:
        target = Path(progress_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        logger.info("mobile.downloader: could not write progress to %s", progress_path)


#: Hosts a mobile download may come FROM and be redirected TO. Suffix matched:
#: an entry matches that host exactly, or any sub-domain of it.
#:
#: ``github.com`` / ``githubusercontent.com`` are LOAD BEARING, not a courtesy:
#: the QA IME is a GitHub release asset and a release download answers 302 to
#: ``objects.githubusercontent.com``, so an allowlist of only Google's and
#: Adoptium's hosts would refuse the one download this lane can make today.
#: Removing them turns
#: ``test_the_github_release_chain_the_ime_really_uses_is_allowed`` red, which is
#: the intended alarm rather than a puzzle.
ALLOWED_HOST_SUFFIXES: tuple[str, ...] = (
    "dl.google.com",
    "adoptium.net",
    "github.com",
    "githubusercontent.com",
)


class RedirectRefused(ValueError):
    """A redirect hop left HTTPS or left the allowlist. Raised PER HOP."""


# WHY THIS STOPS HERE, AND WHERE jira_fetcher DOES NOT
#
# `tools/jira_fetcher.py` fetches a URL a TESTER supplies, with a credential
# attached, and returns the body to a model. That earns its full SSRF stack:
# scheme and DNS checks, public-IP verification and IP pinning on every hop,
# because the attacker chooses the host and the prize is the credential.
#
# This module is a different shape on all three counts:
#
# * The URL is never user-supplied. It is either pinned in `ime_manifest` or
#   read from Google's own SDK repository XML, and every hop must stay inside
#   ALLOWED_HOST_SUFFIXES.
# * No credential is ever attached. There is nothing for a redirected request
#   to leak, which is what IP pinning mainly protects.
# * Integrity does not rest on the host. The IME asset is SHA-256 pinned and
#   SDK components verify against Google's published checksums, so content
#   substitution fails the hash even if a fetch reached the wrong server.
#
# The residual, stated rather than implied: a party controlling DNS for an
# allowlisted host could point a fetch at an address of their choosing,
# including a private one. What they get is a request with no credential whose
# response is written to a cache file and then rejected by the hash -- so the
# realistic outcome is a failed download, not a compromise. That is the reason
# IP pinning is absent, not an oversight; adding it here would be defensible
# but buys little against this threat model. If this module ever gains a
# credential, or ever fetches a URL a tester typed, that calculus inverts and
# the jira_fetcher stack becomes the requirement.


def host_allowed(host: object) -> bool:
    """True when *host* is in :data:`ALLOWED_HOST_SUFFIXES` or below one.

    Sub-domain matching is ``endswith("." + suffix)`` and never
    ``endswith(suffix)``: the second accepts ``evilgithub.com``, which is the
    classic allowlist bypass and the reason this is a function rather than a
    one-line comprehension at the call site.
    """
    name = str(host or "").strip().lower().rstrip(".")
    if not name:
        return False
    for suffix in ALLOWED_HOST_SUFFIXES:
        if name == suffix or name.endswith("." + suffix):
            return True
    return False


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Validates EVERY redirect hop before urllib follows it.

    The check this joins reads ``response.url`` AFTER the whole chain has been
    followed, so a chain that passed through ``http://evil`` and ENDED on an
    allowed HTTPS host satisfied it -- by which point the bytes had already
    travelled over the hostile hop. ``redirect_request`` is the one method
    urllib calls for each 30x, so a refusal here is a refusal per hop.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Refuse the hop, or hand it to the base implementation."""
        parts = urlsplit(str(newurl or ""))
        if parts.scheme != "https":
            raise RedirectRefused(
                "Refusing to follow a redirect off HTTPS (hop to "
                + (parts.scheme or "an empty scheme")
                + "://"
                + (parts.hostname or "an empty host")
                + "). Nothing was written."
            )
        if not host_allowed(parts.hostname):
            raise RedirectRefused(
                "Refusing to follow a redirect to "
                + (parts.hostname or "an empty host")
                + ": the mobile lane downloads only from "
                + ", ".join(ALLOWED_HOST_SUFFIXES)
                + ". Nothing was written."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, start: int):
    """Open *url* with every redirect hop validated by :class:`_GuardedRedirects`.

    An OPENER rather than ``urlopen``: ``urlopen`` uses the default handler
    chain, which follows redirects without asking anybody, and offers no hook to
    inspect a hop. The origin's scheme and host are checked in :func:`download`
    before this is called, so nothing here ever opens an unvalidated URL.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "qa-agents-mobile"})
    if start > 0:
        request.add_header("Range", "bytes=" + str(start) + "-")
    opener = urllib.request.build_opener(_GuardedRedirects())
    return opener.open(request, timeout=SOCKET_TIMEOUT_S)


def download(
    url: str,
    dest: str | Path,
    sha256: str,
    payload_bytes: int = 0,
    progress_path: str | Path | None = None,
    phase: str = "download",
) -> dict:
    """Fetch *url* to *dest*, verifying SHA-256. Resumable, never raises.

    Returns ``{"error", "content": {"path", "bytes", "cached", "verified"}}``.
    A *dest* that already exists and already hashes correctly is returned
    immediately with ``cached=True`` and no request is made -- which is also
    what makes a second provisioner run issue no downloads.

    THE KILL-SWITCH LIVES HERE, at the innermost public function that fetches:
    a guard on a caller is only as good as the list of callers, and that list
    grew by one in the very commit meant to close that class.
    """
    try:
        if not settings.qa_mobile_run_enabled:
            return {
                "error": (
                    "Refusing to download: the mobile lane needs "
                    "`QA_MOBILE_RUN_ENABLED=true` in `.env`. Nothing was "
                    "fetched."
                ),
                "content": None,
            }
        parts = urlsplit(str(url))
        if parts.scheme != "https":
            return {
                "error": (
                    "Refusing to download over "
                    + (parts.scheme or "an empty scheme")
                    + ": the mobile lane fetches over HTTPS only."
                ),
                "content": None,
            }
        if not parts.hostname:
            return {
                "error": "Refusing to download from a URL with no host.",
                "content": None,
            }
        if not host_allowed(parts.hostname):
            return {
                "error": (
                    "Refusing to download from "
                    + str(parts.hostname)
                    + ": the mobile lane downloads only from "
                    + ", ".join(ALLOWED_HOST_SUFFIXES)
                    + ". Nothing was fetched."
                ),
                "content": None,
            }
        name = (parts.path or "").rsplit("/", 1)[-1] or "the requested file"
        if not valid_sha256(sha256):
            return {
                "error": (
                    "Refusing to download "
                    + name
                    + ": no valid SHA-256 is pinned for it (got "
                    + repr(str(sha256 or "")[:16])
                    + "). An unverifiable download is not attempted."
                ),
                "content": None,
            }
        want = str(sha256).strip().lower()
        dest_path = Path(dest)
        if dest_path.is_file() and digest_file(dest_path) == want:
            return {
                "error": None,
                "content": {
                    "path": str(dest_path),
                    "bytes": dest_path.stat().st_size,
                    "cached": True,
                    "verified": True,
                },
            }
        disk = (check_disk(payload_bytes, dest_path.parent) or {}).get("content") or {}
        if not disk.get("ok", False) and required_bytes(payload_bytes) > 0:
            return {
                "error": str(disk.get("detail") or "insufficient disk space"),
                "content": None,
            }
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        part = dest_path.with_name(dest_path.name + ".part")
        start = part.stat().st_size if part.is_file() else 0
        try:
            response = _open(str(url), start)
        except urllib.error.HTTPError as exc:
            if start and exc.code in (400, 416, 501):
                # The server will not resume. Start over rather than fail: a
                # stale .part from an aborted run must not wedge the lane.
                part.unlink(missing_ok=True)
                start = 0
                response = _open(str(url), 0)
            else:
                raise
        with response:
            final = str(getattr(response, "url", "") or url)
            if urlsplit(final).scheme != "https":
                return {
                    "error": (
                        "Refusing to follow a redirect off HTTPS (to "
                        + (urlsplit(final).scheme or "an empty scheme")
                        + "). Nothing was written."
                    ),
                    "content": None,
                }
            if start and int(getattr(response, "status", 200) or 200) != 206:
                # A 200 to a Range request means the whole body is coming.
                start = 0
            mode = "ab" if start else "wb"
            try:
                declared = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                declared = 0
            total = start + declared
            got = start
            last_write = 0.0
            with open(part, mode) as handle:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    handle.write(block)
                    got += len(block)
                    now = time.time()
                    if now - last_write >= _PROGRESS_INTERVAL_S:
                        last_write = now
                        write_progress(
                            progress_path,
                            {
                                "phase": phase,
                                "pct": int(got * 100 / total) if total else 0,
                                "bytes": got,
                                "total": total,
                                "message": "downloading " + name,
                                "error": None,
                                "pid": os.getpid(),
                                "updated": now,
                            },
                        )
        actual = digest_file(part)
        if actual != want:
            size = part.stat().st_size
            part.unlink(missing_ok=True)
            return {
                "error": (
                    "Hash mismatch for "
                    + name
                    + ": expected SHA-256 "
                    + want
                    + " but the "
                    + str(size)
                    + " bytes received hash to "
                    + actual
                    + ". The partial file was deleted and nothing was installed."
                ),
                "content": None,
            }
        os.replace(part, dest_path)
        return {
            "error": None,
            "content": {
                "path": str(dest_path),
                "bytes": dest_path.stat().st_size,
                "cached": False,
                "verified": True,
            },
        }
    except RedirectRefused as exc:
        # Ahead of the ValueError clause below on purpose: a refused hop is a
        # decision this module made, and the tester must read it as a refusal
        # rather than as "the download failed".
        logger.warning("mobile.downloader: refused a redirect: %s", exc)
        return {"error": str(exc), "content": None}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("mobile.downloader: %s failed: %s", str(url)[:120], exc)
        return {
            "error": "Download of " + str(url)[:200] + " failed: " + str(exc),
            "content": None,
        }
    except Exception as exc:
        logger.exception("mobile.downloader.download failed")
        return {"error": str(exc), "content": None}
