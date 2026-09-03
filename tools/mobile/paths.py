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
import shutil
import sys
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

#: Sub-directories of the cache root. ``sdk``/``jre`` hold provisioned
#: toolchains, ``ime`` the verified APK, ``runs`` per-run checkpoints,
#: ``state`` the detached provisioner's progress file, ``locks`` lock files.
SUBDIRS: tuple[str, ...] = ("sdk", "jre", "ime", "runs", "state", "locks")

#: Parts of the default root, relative to the user's home directory.
DEFAULT_ROOT_PARTS: tuple[str, ...] = (".qa-agents", "mobile")


def _configured() -> str:
    """The operator's configured cache dir, or ``""``.

    ``settings`` is the primary source (it is what ``.env`` feeds). The direct
    ``os.environ`` read is a belt for exactly one case: the DETACHED provisioner
    is a fresh interpreter, and if its ``Settings`` construction degrades for an
    unrelated field the whole config resets to class defaults -- at which point
    the cache dir would silently move under the running server's feet.
    """
    raw = ""
    try:
        raw = str(getattr(settings, "qa_mobile_cache_dir", "") or "").strip()
    except Exception:  # pragma: no cover - settings is a module singleton
        raw = ""
    if not raw:
        raw = (os.environ.get("QA_MOBILE_CACHE_DIR") or "").strip()
    return raw


def cache_root() -> Path:
    """The mobile cache root. Pure: creates nothing, never raises."""
    raw = _configured()
    if raw:
        try:
            return Path(raw).expanduser()
        except Exception:
            logger.warning(
                "mobile.paths: unusable QA_MOBILE_CACHE_DIR=%r -- using the default",
                raw,
            )
    return Path.home().joinpath(*DEFAULT_ROOT_PARTS)


def sub(name: str) -> Path:
    """Path of a cache sub-directory. Creates nothing."""
    return cache_root() / str(name)


def state_file(name: str) -> Path:
    """Path of a file in ``state/``."""
    return sub("state") / str(name)


def run_dir(run_id: str) -> Path:
    """Path of one run's directory in ``runs/``."""
    return sub("runs") / str(run_id)


def ensure_tree() -> dict:
    """Create the root and every sub-directory, 0700 where the OS allows it."""
    try:
        root = cache_root()
        root.mkdir(parents=True, exist_ok=True)
        try:
            root.chmod(0o700)
        except OSError:
            # Windows ignores POSIX modes and some network volumes refuse the
            # call outright. A cache we cannot tighten is still usable, and
            # nothing credential-shaped is ever written into it.
            logger.info("mobile.paths: could not tighten permissions on %s", root)
        dirs: dict[str, str] = {}
        for name in SUBDIRS:
            child = root / name
            child.mkdir(parents=True, exist_ok=True)
            dirs[name] = str(child)
        return {"error": None, "content": {"root": str(root), "dirs": dirs}}
    except Exception as exc:
        logger.exception("mobile.paths.ensure_tree failed")
        return {"error": str(exc), "content": None}


def ownership(target: Path | None = None) -> dict:
    """``{ok, checked, detail, fix}`` -- is the mobile cache the USER's own?

    A cache root owned by somebody else is refused BEFORE anything is written
    into it. On a shared or admin-provisioned machine that directory can belong
    to root, and the failure it otherwise produces is a half-provisioned SDK
    whose permission error is buried in a detached process's log.

    **On Windows this check does not run, and that is REPORTED rather than
    hidden.** ``os.stat().st_uid`` is 0 for every file there, so a uid
    comparison would pass unconditionally -- which is worse than not checking,
    because a caller cannot tell the two apart. ``checked=False`` is the
    disclosure, and ``docs/MOBILE_TESTING.md`` repeats it. The Windows
    equivalent is the ACL on the user's own profile directory, where the default
    cache lives; enforcing an ACL from here is separate work with no coverage
    available on a POSIX host.

    An undeterminable owner is reported as NOT ours, on the same rule
    :func:`free_bytes` follows: "unknown" must never read as "fine".
    """
    try:
        root = Path(target) if target is not None else cache_root()
        if sys.platform == "win32":
            return {
                "error": None,
                "content": {
                    "ok": True,
                    "checked": False,
                    "detail": (
                        "Ownership is not checked on Windows: a POSIX owner id "
                        "carries no meaning there. The cache at "
                        + str(root)
                        + " sits under the user profile, whose ACL restricts it."
                    ),
                    "fix": "",
                },
            }
        probe = root
        for _ in range(8):
            if probe.exists():
                break
            parent = probe.parent
            if parent == probe:
                break
            probe = parent
        owner = int(os.stat(str(probe)).st_uid)
        mine = int(os.getuid())
        if owner == mine:
            return {
                "error": None,
                "content": {
                    "ok": True,
                    "checked": True,
                    "detail": str(probe) + " is owned by uid " + str(mine),
                    "fix": "",
                },
            }
        return {
            "error": None,
            "content": {
                "ok": False,
                "checked": True,
                "detail": (
                    "The mobile cache path "
                    + str(probe)
                    + " is owned by uid "
                    + str(owner)
                    + ", not by you (uid "
                    + str(mine)
                    + "), so nothing was downloaded, installed or created there."
                ),
                "fix": (
                    "Take ownership of that directory, or point the lane at a "
                    "path you own with QA_MOBILE_CACHE_DIR in .env and restart "
                    "the MCP server."
                ),
            },
        }
    except Exception as exc:
        logger.warning("mobile.paths: could not read cache ownership: %s", exc)
        return {
            "error": None,
            "content": {
                "ok": False,
                "checked": False,
                "detail": (
                    "The owner of the mobile cache path could not be determined "
                    "(" + str(exc)[:120] + "), so it is treated as NOT ours."
                ),
                "fix": (
                    "Check that the path in QA_MOBILE_CACHE_DIR exists and is "
                    "readable, or unset it to use ~/.qa-agents/mobile."
                ),
            },
        }


def free_bytes(target: Path | None = None) -> int:
    """Free bytes on the volume holding *target* (default: the cache root).

    Returns ``-1`` when it cannot be determined, so a caller discloses
    "unknown" rather than treating it as "plenty". The walk up to an existing
    ancestor matters on a first run: the cache root does not exist yet, and
    ``disk_usage`` on a missing path raises.
    """
    probe = target if target is not None else cache_root()
    try:
        probe = Path(probe)
    except Exception:
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
