"""ONE rule for which packages may own learned knowledge (C09, C10).

System packages (the Play Store, browsers, Google services, settings, vendor
system apps) and the platform's transient surfaces (App Tester, installer,
launcher) never get a knowledge db or a flow file: the run still runs, with
knowledge OFF. Knows no app; the lists are Android platform families.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tools.mobile import paths, system_packages, target_pin

logger = logging.getLogger("tools.mobile.knowledge_scope")

_STORE_FILES = ("knowledge.db", "knowledge.db-wal", "knowledge.db-shm", "flows.json")
_DONE: list[bool] = []


def knowledge_scope(package: object) -> str:
    """``""`` when *package* may own knowledge, else why it may not."""
    name = str(package or "").strip()
    if target_pin.transient(name):
        return "transient package"
    return "system package" if system_packages.is_system(name) else ""


def allowed(package: object) -> bool:
    """True when *package* may have a knowledge db and flows."""
    return not knowledge_scope(package)


def _drop(folder: Path) -> None:
    for name in _STORE_FILES:
        try:
            (folder / name).unlink(missing_ok=True)
        except OSError:
            logger.warning("knowledge scope: could not drop %s/%s", folder, name)


def _stale(folder: Path) -> bool:
    if not folder.is_dir() or folder.is_symlink() or allowed(folder.name):
        return False
    return any((folder / name).exists() for name in _STORE_FILES)


def purge() -> list[str]:
    """Drop the knowledge db and flows of every out-of-scope package. Idempotent."""
    gone: list[str] = []
    try:
        for folder in sorted(paths.sub("apps").iterdir()):
            if _stale(folder):
                _drop(folder)
                gone.append(folder.name)
    except OSError:
        logger.info("knowledge scope: nothing to purge", exc_info=True)
    if gone:
        logger.info("knowledge scope: purged %s", ", ".join(gone))
    if gone:
        logger.info("knowledge scope: purged %s", ", ".join(gone))
    return gone


def purge_once() -> None:
    """``purge`` once per process (first knowledge run)."""
    if not _DONE:
        _DONE.append(True)
        purge()
