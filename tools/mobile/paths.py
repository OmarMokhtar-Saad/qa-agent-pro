"""Filesystem layout for the mobile lane: one shared cache under the home dir.

Everything the mobile lane downloads or writes lives under a single root, so a
tester can delete one directory and be back to a clean machine. The root is
``~/.qa-agents/mobile`` unless ``QA_MOBILE_CACHE_DIR`` names another path.

House rules obeyed here: no ``print``, and every public function returns
``{"error", "content"}`` rather than raising. The one exception in SHAPE is
:func:`cache_root` / :func:`sub` / :func:`run_dir`, which return a ``Path``
because they are pure path arithmetic that touches no filesystem and therefore
has nothing to fail at -- a dict there would only force every caller to unwrap
a value that cannot be an error.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

from config.settings_guard import never_raise
from tools.mobile import platform_info
from tools.mobile.paths_conf import _configured
from tools.mobile.paths_own import (
    _existing_ancestor,
    _ownership_ok,
    _ownership_refused,
    _ownership_unknown,
    _ownership_windows,
)
from tools.mobile.paths_tree import build_tree
from tools.process_env import get_env_str, home_dir  # noqa: F401

logger = logging.getLogger(__name__)

#: Sub-directories of the cache root. ``sdk``/``jre`` hold provisioned
#: toolchains, ``ime`` the verified APK, ``runs`` per-run checkpoints,
#: ``state`` the detached provisioner's progress file, ``locks`` lock files.
SUBDIRS: tuple[str, ...] = (
    "sdk",
    "jre",
    "ime",
    "runs",
    "state",
    "locks",
    # The API-capture lane's root: the CA, the trust ledger and the live-proxy
    # markers. It is listed HERE rather than created by that package so there is
    # one tree made in one place; tools/mobile_capture/paths.py derives every
    # path below it and invents no root of its own.
    "capture",
)

#: Parts of the default root, relative to the user's home directory.
DEFAULT_ROOT_PARTS: tuple[str, ...] = (".qa-agents", "mobile")


def cache_root() -> Path:
    """The mobile cache root. Pure: creates nothing, never raises."""
    raw = _configured()
    if raw:
        try:
            return Path(raw).expanduser()
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning(
                "mobile.paths: unusable QA_MOBILE_CACHE_DIR=%r -- using the default",
                raw,
            )
    return home_dir().joinpath(*DEFAULT_ROOT_PARTS)


#: What may become a ``runs/<id>`` path segment (also ``run_store.valid_run_id``).
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def sub(name: str) -> Path:
    """Path of a cache sub-directory. Creates nothing."""
    return cache_root() / str(name)


def state_file(name: str) -> Path:
    """Path of a file in ``state/``."""
    return sub("state") / str(name)


def run_dir(run_id: str) -> Path:
    """Path of one run's directory in ``runs/``.

    *run_id* becomes a path segment, so one that could leave ``runs/`` (``..``,
    a separator, an empty string) is refused with ``ValueError``.
    """
    segment = str(run_id)
    if not RUN_ID_RE.match(segment):
        raise ValueError(f"not a usable run id: {segment!r}")
    return sub("runs") / segment


def ensure_tree() -> dict:
    """Create the root and every sub-directory, 0700 where the OS allows it."""
    try:
        root = cache_root()
        dirs = build_tree(root, SUBDIRS)
        return {"error": None, "content": {"root": str(root), "dirs": dirs}}
    except Exception as exc:
        logger.exception("mobile.paths.ensure_tree failed")
        return {"error": str(exc), "content": None}


# ``{ok, checked, detail, fix}`` -- is the mobile cache the USER's own?
#
# A cache root owned by somebody else is refused BEFORE anything is written
# into it. On a shared or admin-provisioned machine that directory can belong
# to root, and the failure it otherwise produces is a half-provisioned SDK
# whose permission error is buried in a detached process's log.
#
# **On Windows this check does not run, and that is REPORTED rather than
# hidden.** ``os.stat().st_uid`` is 0 for every file there, so a uid
# comparison would pass unconditionally -- which is worse than not checking,
# because a caller cannot tell the two apart. ``checked=False`` is the
# disclosure -- and it had NO consumer until ``preflight``'s
# ``cache_ownership`` check was added to render it: both readers of this
# function (``provisioner.run`` and ``provisioner.start_detached``) branch on
# ``ok`` alone, so on Windows the disclosure reached no tester at all while
# this docstring claimed otherwise. ``docs/MOBILE_TESTING.md`` now repeats it
# too. The Windows
# equivalent is the ACL on the user's own profile directory, where the default
# cache lives; enforcing an ACL from here is separate work with no coverage
# available on a POSIX host.
#
# An undeterminable owner is reported as NOT ours, on the same rule
# :func:`free_bytes` follows: "unknown" must never read as "fine".
def ownership(target: Path | None = None) -> dict:
    """``{ok, checked, detail, fix}`` -- is the mobile cache the USER's own?"""
    with never_raise(
        logger,
        "mobile.paths: could not read cache ownership: %s",
        level=logging.WARNING,
        with_exc=True,
    ) as g:
        root = Path(target) if target is not None else cache_root()
        if platform_info.is_windows():
            return _ownership_windows(root)
        probe = _existing_ancestor(root)
        owner = int(os.stat(str(probe)).st_uid)
        mine = int(os.getuid())
        if owner == mine:
            return _ownership_ok(probe, mine)
        return _ownership_refused(probe, owner, mine)
    return _ownership_unknown(g.exc)


# Free bytes on the volume holding *target* (default: the cache root).
#
# Returns ``-1`` when it cannot be determined, so a caller discloses
# "unknown" rather than treating it as "plenty". The walk up to an existing
# ancestor matters on a first run: the cache root does not exist yet, and
# ``disk_usage`` on a missing path raises.
def free_bytes(target: Path | None = None) -> int:
    """Free bytes on the volume holding *target*, or ``-1`` when unknown."""
    probe = target if target is not None else cache_root()
    try:
        probe = Path(probe)
    except (TypeError, ValueError):
        return -1
    for _ in range(8):
        try:
            return int(shutil.disk_usage(probe).free)
        except OSError:
            parent = probe.parent
            if parent == probe:
                break
            probe = parent
    logger.info("mobile.paths: could not read free space near %s", target)
    return -1
