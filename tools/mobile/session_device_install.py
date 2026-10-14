"""Mobile session: spawn a detached ``adb install``. Cut from tools/mobile/session_device.py (w17 sessA)."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from tools.device_manager import _valid_device_id, valid_package_name
from tools.mobile import adb, downloader, paths, platform_info
from tools.mobile.session_device_record import _install_state_path

logger = logging.getLogger("tools.mobile.session")


def _refusal(message: str) -> dict:
    return {"error": message, "content": None}


def _refuse_ids(serial: str, package: str) -> dict | None:
    """The refusal for a package name or device id that is not one, else None."""
    if not valid_package_name(package):
        return _refusal("Refusing " + repr(str(package)[:60]) + " as a package name.")
    if not _valid_device_id(serial):
        return _refusal("Refusing " + repr(str(serial)[:40]) + " as a device id.")
    return None


def _refuse_apk(path: Path) -> dict | None:
    """The refusal for a path that is not an installable .apk, else None."""
    if path.suffix.lower() != ".apk" or not path.is_file():
        return _refusal(
            "No .apk file at "
            + str(path)[:200]
            + ". Give the full path to the file, and nothing is installed."
        )
    problem = adb.apk_problem(path)
    if problem:
        return _refusal(
            "Not installing "
            + str(path)[:200]
            + ": "
            + problem
            + ". Nothing was installed."
        )
    return None


def _install_refusal(serial: str, package: str, path: Path) -> dict | None:
    """The error reply for an install that must not start, else None."""
    return _refuse_ids(serial, package) or _refuse_apk(path)


def _spawn_install(serial: str, path: Path) -> object:
    """Start ``adb install -r -g`` detached and return the process."""
    command = [adb.resolve_adb(), "-s", str(serial), "install", "-r", "-g", str(path)]
    return subprocess.Popen(command, **platform_info.detached_devnull_kwargs())  # noqa: S603 - argv, no shell


def _install_payload(serial: str, package: str, path: Path) -> dict:
    """What an install record says before the process is known."""
    return {"package": str(package), "serial": str(serial), "apk": str(path)}


# ``adb.install`` is bounded at 300s, which no tool call may hold, so the
# install outlives this call by design. Completion is NOT inferred from the
# process: :func:`install_state` asks the device whether the package is
# present, because a pid that exited proves nothing and an install that failed
# must never read as success.
#
# Reads NO switch: the mobile lane ships on with no flag. The handler that
# normally calls this checks the lane predicate and ``apply``, and those are
# guards on that CALLER only, while this function spawns a detached install
# on the tester's device -- an effect that outlives the call. A new caller
# has to bring its own consent; nothing here asks for it.
def start_install(serial: str, apk_path: str, package: str) -> dict:
    """Spawn ``adb install`` DETACHED and record what was started."""
    try:
        path = Path(str(apk_path or "")).expanduser()
        refusal = _install_refusal(serial, package, path)
        if refusal:
            return refusal
        paths.ensure_tree()
        proc = _spawn_install(serial, path)
        payload = _install_payload(serial, package, path)
        payload["pid"] = int(getattr(proc, "pid", 0) or 0)
        payload["started"] = time.time()
        downloader.write_progress(_install_state_path(), payload)
        return {"error": None, "content": payload}
    except Exception as exc:
        logger.exception("mobile.session.start_install failed")
        return {"error": str(exc), "content": None}
