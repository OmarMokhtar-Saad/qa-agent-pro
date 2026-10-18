"""Updater GitHub release lookup: headers, rate-limit note and TTL cache (split from tools/updater.py)."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from config.settings_guard import never_raise
from tools import http_port
from tools.updater_log import logger
from tools.updater_pick import highest_server_release, is_server_release

_GITHUB_API = "https://api.github.com"


def _auth_headers(token: str) -> dict:
    """Build request headers. The bearer token is added ONLY when set, and this
    is the single place a token ever touches a header — it is never logged."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# GitHub answers an exhausted quota with 403 (primary limit) or 429
# (secondary), and in BOTH cases sets `x-ratelimit-remaining: 0`. Before
# TICKET-5138 (2026-08-21) that landed in run_update_check's blanket
# `except Exception` as "Startup update check failed (...)" -- the SAME line a
# DNS failure produces -- so the live install whose quota was spent (Cursor
# client log, 21:33:55) read as broken rather than BLIND, and the remedy was
# nowhere in the record. A 401, a 404, or a 403 that is NOT a quota must stay
# a plain error, so the remaining-header check is what separates the two.
_RATE_LIMIT_STATUSES = (403, 429)


def _quota_headers(exc: Exception) -> Optional[Any]:
    """The response headers when ``exc`` is an exhausted-quota refusal, else None."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    if getattr(resp, "status_code", None) not in _RATE_LIMIT_STATUSES:
        return None
    headers = getattr(resp, "headers", None) or {}
    if str(headers.get("x-ratelimit-remaining", "")).strip() != "0":
        return None
    return headers


def _quota_note(headers: Any) -> str:
    """Short human note (authenticated or not, time to reset) from quota headers."""
    limit = str(headers.get("x-ratelimit-limit", "")).strip()
    authed = limit.isdigit() and int(limit) > 60
    who = "authenticated" if authed else "unauthenticated, 60/hour"
    wait = ""
    reset = str(headers.get("x-ratelimit-reset", "")).strip()
    if reset.isdigit():
        mins = max(0, int((int(reset) - time.time()) // 60))
        wait = f", resets in ~{mins} min"
    return f"{who}{wait}"


# Short human note when ``exc`` is a GitHub rate-limit refusal, else None.
#
# Never raises, and never reads or logs the token. A missing or garbled
# header degrades to a LESS SPECIFIC note; anything unparseable enough to
# throw returns None, so the caller falls back to the generic error path
# rather than mislabelling an unrelated failure as a rate limit.
def _rate_limit_detail(exc: Exception) -> Optional[str]:
    """Short human note when ``exc`` is a GitHub rate-limit refusal, else None."""
    with never_raise(logger, "rate-limit detail parse failed", exc_info=True):
        headers = _quota_headers(exc)
        return None if headers is None else _quota_note(headers)
    return None


# One GET per process per TTL: startup, qa-doctor and the periodic drift
# watch each hit /releases/latest, so a single session made three identical
# calls within 11 seconds (observed 2026-08-03 22:11:14-22:11:25). 300s keeps
# "publish a release, run qa-doctor" responsive while collapsing the
# burst -- and keeps the network off the qa-doctor hot path within the
# TTL. Only SUCCESS is cached; errors keep raising so callers degrade as
# before and the next call retries for real.
_RELEASE_CACHE_TTL_S = 300.0

_release_cache: dict[str, tuple[float, dict]] = {}

_release_cache_lock = threading.Lock()


def fetch_latest_release(repo: str, token: str, timeout: float) -> Optional[dict]:
    """TTL-cached wrapper over the GitHub release lookup (cache note above).
    Same contract as the uncached fetch: returns the release dict or raises on
    a network/HTTP error (the caller catches and degrades)."""
    now = time.monotonic()
    with _release_cache_lock:
        hit = _release_cache.get(repo)
        if hit and (now - hit[0]) < _RELEASE_CACHE_TTL_S:
            return dict(hit[1])
    data = _fetch_latest_release_uncached(repo, token, timeout)
    if data:
        with _release_cache_lock:
            _release_cache[repo] = (now, dict(data))
    return data


def _fetch_latest_release_uncached(
    repo: str, token: str, timeout: float
) -> Optional[dict]:
    """GET /repos/{repo}/releases/latest. Returns ``{tag_name, zipball_url}`` or
    raises on a network/HTTP error (the caller catches and degrades).

    GitHub's "latest" can be a non-server release (an IME tag published last
    made every install read "Up to date"), so a latest that is not a plain
    ``vX.Y.Z`` is replaced by the highest ``vX.Y.Z`` in the release list."""
    url = f"{_GITHUB_API}/repos/{repo}/releases/latest"
    resp = http_port.get(url, _auth_headers(token), timeout)
    resp.raise_for_status()
    data = resp.json()
    if not is_server_release(data):
        logger.info("latest release %r is not a server tag", data.get("tag_name"))
        data = _highest_listed(repo, token, timeout) or data
    return _release_view(data)


def _highest_listed(repo: str, token: str, timeout: float) -> Optional[dict]:
    """The highest ``vX.Y.Z`` release among the newest 100, or None."""
    url = f"{_GITHUB_API}/repos/{repo}/releases?per_page=100"
    resp = http_port.get(url, _auth_headers(token), timeout)
    resp.raise_for_status()
    return highest_server_release(resp.json() or [])


def _release_view(data: dict) -> dict:
    """``{tag_name, zipball_url, asset_url}`` of a release payload."""
    asset_url = ""
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.endswith(".zip") and asset.get("browser_download_url"):
            asset_url = asset["browser_download_url"]
            break
    return {
        "tag_name": data.get("tag_name"),
        "zipball_url": data.get("zipball_url"),
        "asset_url": asset_url,
    }
