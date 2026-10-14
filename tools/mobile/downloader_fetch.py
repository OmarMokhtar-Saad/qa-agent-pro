"""The transfer side of the downloader: stream into ``.part``, verify, move into place."""

from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import urlsplit

from tools.mobile.downloader_args import DownloadRequest, FetchJob
from tools.mobile.downloader_disk import CHUNK, write_progress
from tools.mobile.downloader_host import host
from tools.mobile.downloader_net import _open_resuming

# WHY THIS STOPS HERE, AND WHERE jira_fetcher DOES NOT
#
# `tools/jira_fetcher.py` fetches a URL a TESTER supplies, with a credential
# attached, and returns the body to a model. That earns its full SSRF stack:
# scheme and DNS checks, public-IP verification and IP pinning on every hop,
# because the attacker chooses the host and the prize is the credential.
#
# The downloader is a different shape on all three counts:
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


#: Progress files are written no more often than this.
_PROGRESS_INTERVAL_S = 1.0


def _declared_total(response, start: int) -> int:
    """Bytes the whole file will have, from the response's ``Content-Length``."""
    try:
        declared = int(response.headers.get("Content-Length") or 0)
    except (TypeError, ValueError, OverflowError):
        declared = 0
    return start + declared


def _progress_row(base: dict, pos: tuple[int, int], now: float) -> dict:
    got, total = pos
    return {
        **base,
        "pct": int(got * 100 / total) if total else 0,
        "bytes": got,
        "total": total,
        "updated": now,
    }


def _stream_to_part(response, job: FetchJob, start: int) -> None:
    """Write the body to the job's part (appending from *start*), reporting progress.

    ``job.base`` holds the progress fields that do not change mid-download.
    """
    total = _declared_total(response, start)
    got = start
    last_write = 0.0
    with open(job.part, "ab" if start else "wb") as handle:
        while True:
            block = response.read(CHUNK)
            if not block:
                break
            handle.write(block)
            got += len(block)
            now = time.time()
            if now - last_write >= _PROGRESS_INTERVAL_S:
                last_write = now
                row = _progress_row(job.base, (got, total), now)
                write_progress(job.progress_path, row)


def _delivered(dest_path: Path, *, cached: bool) -> dict:
    return {
        "error": None,
        "content": {
            "path": str(dest_path),
            "bytes": dest_path.stat().st_size,
            "cached": cached,
            "verified": True,
        },
    }


def _fetch_part(job: FetchJob, start: int) -> str:
    """Stream the job's URL into its part; a refusal text if it left HTTPS, else ``""``."""
    response, start = _open_resuming(job.url, job.part, start)
    with response:
        final = str(getattr(response, "url", "") or job.url)
        if urlsplit(final).scheme != "https":
            return (
                "Refusing to follow a redirect off HTTPS (to "
                + (urlsplit(final).scheme or "an empty scheme")
                + "). Nothing was written."
            )
        if start and int(getattr(response, "status", 200) or 200) != 206:
            # A 200 to a Range request means the whole body is coming.
            start = 0
        _stream_to_part(response, job, start)
    return ""


def _file_name(url: object) -> str:
    return (urlsplit(str(url)).path or "").rsplit("/", 1)[-1] or ("the requested file")


def _hash_mismatch(job: FetchJob, want: str, actual: str) -> dict:
    size = job.part.stat().st_size
    job.part.unlink(missing_ok=True)
    return {
        "error": (
            "Hash mismatch for "
            + _file_name(job.url)
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


def _progress_base(phase: str, url: object) -> dict:
    """The progress fields that stay constant for the whole download."""
    return {
        "phase": phase,
        "message": "downloading " + _file_name(url),
        "error": None,
        "pid": os.getpid(),
    }


def fetch_job(req: DownloadRequest, dest_path: Path) -> FetchJob:
    """The job that fetches *req* into ``<dest>.part``."""
    part = dest_path.with_name(dest_path.name + ".part")
    base = _progress_base(req.phase, req.url)
    return FetchJob(str(req.url), part, req.progress_path, base)


def _fetch_and_install(job: FetchJob, dest_path: Path, want: str) -> dict:
    """Resume into ``<dest>.part``, verify the hash, then move it into place."""
    start = job.part.stat().st_size if job.part.is_file() else 0
    off_https = _fetch_part(job, start)
    if off_https:
        return {"error": off_https, "content": None}
    actual = host().digest_file(job.part)
    if actual != want:
        return _hash_mismatch(job, want, actual)
    os.replace(job.part, dest_path)
    return _delivered(dest_path, cached=False)
