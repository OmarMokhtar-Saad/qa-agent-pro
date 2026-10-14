"""Updater download, gate and apply of a release (split from tools/updater.py)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import NamedTuple, Optional, cast

from tools import settings_port
from tools.updater_binding import _manifest_binding_ok
from tools.updater_gate import _signature_gate_ok
from tools.updater_heal import _healed_outcome, _write_heal_marker
from tools.updater_lock import _update_lock
from tools.updater_log import logger
from tools.updater_swap import apply_update
from tools.updater_zip import ZipSource, download_and_extract


class _Release(NamedTuple):
    """A release to download: what, where from, and how to authenticate."""

    remote: object
    download_url: str
    token: str
    timeout: float


class _Found(NamedTuple):
    """What the release check learned: remote tag, local version, archive URL."""

    remote: str
    local: Optional[str]
    download_url: Optional[str]


def _release_of(found: _Found) -> _Release:
    """The download request for *found*, with the configured token and timeout."""
    token = (settings_port.get().github_token or "").strip()
    timeout = settings_port.get().qa_update_timeout
    return _Release(found.remote, cast("str", found.download_url), token, timeout)


# ``apply_update``, serialised across OS processes (2026-09-03, D6).
#
# Only ONE racing process actually applies; the others log a clear line and
# return ``None`` WITHOUT raising, so a race never surfaces as a
# "MANIFEST.sha256 does not match" abort -- it surfaces as nothing, because
# the winner already did the work. A real apply failure still propagates
# from :func:`apply_update` unchanged.
def apply_update_locked(
    new_tree: Path, install_dir: Path, version: str = "update"
) -> Optional[Path]:
    """``apply_update``, serialised across OS processes (2026-09-03, D6)."""
    with _update_lock(install_dir) as acquired:
        if not acquired:
            logger.info(
                "tools.updater: another process is already applying an update "
                "to %s; skipping this pass.",
                install_dir,
            )
            return None
        return apply_update(new_tree, install_dir, version=version)


# C1: a passed gate only proves the manifest's signature; bind
# that manifest to the actual tree bytes before trusting the swap.
def _update_refused(new_tree: Path) -> bool:
    """True when the release signature or its manifest binding is not trusted."""
    return not (
        _signature_gate_ok(
            new_tree,
            require=settings_port.get().qa_update_require_signature,
            context="update",
        )
        and _manifest_binding_ok(new_tree)
    )


def _download_verify_apply(install_dir: Path, rel: _Release) -> Optional[str]:
    """Download, gate and swap a release in; ``None`` means it was applied."""
    with tempfile.TemporaryDirectory(prefix="qa-update-") as tmp:
        new_tree = download_and_extract(
            ZipSource(rel.download_url, rel.token, rel.timeout), Path(tmp)
        )
        if _update_refused(new_tree):
            return "error"
        version = str(rel.remote).lstrip("vV")
        applied = apply_update_locked(new_tree, install_dir, version=version)
        if applied is None:
            return "update-skipped"
    return None


def _heal_gate_refuses(new_tree: Path) -> bool:
    """True (after logging) when the heal tree's signature or binding is not trusted."""
    if _signature_gate_ok(
        new_tree,
        require=settings_port.get().qa_update_require_signature,
        context="self-heal",
    ) and _manifest_binding_ok(new_tree):
        return False
    logger.warning(
        "Self-heal aborted: release signature/manifest not "
        "trusted; leaving current files in place."
    )
    return True


# Re-download the matching release and swap it in. Returns the status:
# ``"healed" | "heal-skipped" | "heal-aborted"``.
def _heal_from_release(install_dir: Path, rel: _Release) -> str:
    """Re-download the matching release and swap it in; returns the status."""
    _write_heal_marker(install_dir, rel.remote, "attempted")
    with tempfile.TemporaryDirectory(prefix="qa-heal-") as tmp:
        new_tree = download_and_extract(
            ZipSource(rel.download_url, rel.token, rel.timeout), Path(tmp)
        )
        if _heal_gate_refuses(new_tree):
            return "heal-aborted"
        version = "heal-" + str(rel.remote).lstrip("vV")
        if apply_update_locked(new_tree, install_dir, version=version) is None:
            return "heal-skipped"
        _write_heal_marker(install_dir, rel.remote, _healed_outcome(install_dir))
        return "healed"
