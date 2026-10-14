"""Updater startup check: entry point and release decision (split from tools/updater.py)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Optional, TypedDict

import httpx

from config.settings_guard import never_raise
from tools import settings_port
from tools.updater_env import _pip_install as pip_install
from tools.updater_env import migrate_env
from tools.updater_github import _rate_limit_detail, fetch_latest_release
from tools.updater_keys import _check_embedded_pubkey
from tools.updater_log import logger
from tools.updater_manifest import lock_files
from tools.updater_release import _download_verify_apply, _Found, _release_of
from tools.updater_selfheal import _lock_and_heal
from tools.updater_version import _local_version as local_version
from tools.updater_version import is_newer

if TYPE_CHECKING:
    from typing_extensions import Unpack

_INSTALL_DIR = Path(__file__).resolve().parent.parent


class _UpdateOptions(TypedDict, total=False):
    """The keyword arguments field-deployed launchers pass to ``run_update_check``."""

    force: bool
    repo_override: Optional[str]
    lock_override: Optional[bool]


class _Request(NamedTuple):
    """The caller's arguments to ``run_update_check``."""

    install_dir: Path
    force: bool
    repo_override: Optional[str]
    lock_override: Optional[bool]


def _log_check_failed(exc: Exception) -> None:
    """Log that the startup update check failed."""
    logger.warning("Startup update check failed (%s) — starting current version.", exc)


# Status for an HTTP failure: an exhausted GitHub quota is not a failure
# to reach GitHub, but a non-quota 403/401/404 stays a plain ``"error"``.
def _http_error_status(exc: httpx.HTTPStatusError) -> str:
    """Status for an HTTP failure: ``rate-limited`` for a spent quota, else ``error``."""
    detail = _rate_limit_detail(exc)
    if detail is None:
        _log_check_failed(exc)
        return "error"
    logger.warning(
        "Startup update check hit the GitHub API rate limit (%s) — "
        "auto-update is BLIND until it resets, not broken, and this "
        "server is starting the version already on disk. Set GITHUB_TOKEN "
        "in .env to raise the limit from 60 to 5000 requests/hour.",
        detail,
    )
    return "rate-limited"


def _after_swap(install_dir: Path, lock: bool) -> None:
    """Post-swap steps: dependencies, ``.env`` migration, optional re-lock."""
    pip_install(install_dir)
    migrate_env(install_dir)
    if lock:
        lock_files(install_dir)


# Prefer the uploaded release ASSET over the auto-generated source
# zipball so GitHub's per-asset download_count tracks version adoption;
# fall back to the zipball when no asset was attached.
def _download_url(release: dict) -> Optional[str]:
    """The archive URL to download for *release*."""
    zipball = release.get("zipball_url")
    return release.get("asset_url") or zipball


def _update_to_newer(install_dir: Path, found: _Found, lock: bool) -> str:
    """Install a release newer than the local one; returns the status string."""
    if not found.download_url:
        logger.warning(
            "Release %s has no downloadable archive - skipping update.",
            found.remote,
        )
        return "error"
    logger.info(
        "Newer release %s available (local=%s) — updating.", found.remote, found.local
    )
    refused = _download_verify_apply(install_dir, _release_of(found))
    if refused is not None:
        return refused
    _after_swap(install_dir, lock)
    logger.info("Update to %s complete.", found.remote)
    return "updated"


def _check_release(install_dir: Path, repo: str, lock: bool) -> str:
    """Fetch the latest release and update, heal or report up-to-date."""
    token = (settings_port.get().github_token or "").strip()
    local = local_version(install_dir)
    release = fetch_latest_release(repo, token, settings_port.get().qa_update_timeout)
    if not release or not release.get("tag_name"):
        logger.warning("No release found for %s — starting current version.", repo)
        return "error"
    found = _Found(release["tag_name"], local, _download_url(release))
    if is_newer(found.remote, local):
        return _update_to_newer(install_dir, found, lock)
    status = "up-to-date"
    if lock:
        status = _lock_and_heal(install_dir, found)
    logger.info("Up to date (local=%s, latest=%s).", local, found.remote)
    return status


# QA_AUTO_UPDATE_ENABLED was DELETED on 2026-08-13 (flag-surface
# reduction, batch 6) and hardcoded OFF, so a check happens only when a
# caller FORCES it. The distribution launcher does exactly that
# (force=True), and its precedence is unchanged -- what is gone is a
# developer checkout's ability to arm the check from .env.
#
# QA_CODE_LOCK_ENABLED was DELETED on 2026-08-13 and hardcoded OFF for a
# developer checkout; lock_override still wins, which is how the
# distribution launcher (lock_override=True) keeps the lock working.
def _checked(req: _Request) -> str:
    """Run the update check for *req*; may raise (the caller absorbs it)."""
    _check_embedded_pubkey()
    if not req.force:
        logger.info("Auto-update is off unless the caller forces it.")
        return "disabled"
    repo = (req.repo_override or settings_port.get().qa_update_repo or "").strip()
    if not repo:
        logger.warning(
            "An update check was forced but no repo is configured (QA_UPDATE_REPO empty) — skipping update."
        )
        return "no-repo"
    lock = False if req.lock_override is None else req.lock_override
    return _check_release(req.install_dir, repo, lock)


def _request_of(install_dir: Optional[Path], options: _UpdateOptions) -> _Request:
    """Build the request; an unknown option raises TypeError like a bad keyword."""
    unknown = set(options) - set(_UpdateOptions.__annotations__)
    if unknown:
        raise TypeError(
            f"run_update_check() got unexpected keyword(s): {sorted(unknown)}"
        )
    return _Request(
        _INSTALL_DIR if install_dir is None else install_dir,
        options.get("force", False),
        options.get("repo_override"),
        options.get("lock_override"),
    )


# Startup self-update + integrity pass. Returns a status string and NEVER
# raises: ``"disabled" | "no-repo" | "up-to-date" | "updated" | "healed" |
# "heal-aborted" | "rate-limited" | "error"``. On any failure the caller starts
# the current version.
#
# ``force`` / ``repo_override`` / ``lock_override`` let a distribution
# launcher mandate the check regardless of local ``.env`` toggles; developer
# checkouts keep the opt-in settings defaults (all OFF).
#
# The HTTPStatusError clause sits ahead of the blanket clause ON PURPOSE: an
# exhausted GitHub quota is not a failure to reach GitHub. Both directions
# matter -- a non-quota 403/401/404 falls through to the generic message and
# "error", which is what keeps this branch from becoming "every HTTP error is
# a rate limit".
#
# Field-deployed launchers call it with the keywords of ``_UpdateOptions``;
# an unknown keyword still raises TypeError at the call, as a signature would.
def run_update_check(
    install_dir: Optional[Path] = None, **options: Unpack[_UpdateOptions]
) -> str:
    """Startup self-update + integrity pass; returns a status string, never raises."""
    req = _request_of(install_dir, options)
    with never_raise() as g:
        try:
            return _checked(req)
        except httpx.HTTPStatusError as exc:
            return _http_error_status(exc)
    assert isinstance(g.exc, Exception)
    _log_check_failed(g.exc)
    return "error"
