"""Installing and removing an APK: ``apk_problem``, ``install``, ``uninstall`` (moved from adb.py)."""

from __future__ import annotations

import logging
from pathlib import Path

from tools.device_ids import valid_package_name
from tools.mobile.adb_consts import APK_MAGIC
from tools.mobile.adb_host import host
from tools.mobile.install_errors import parse_failure

logger = logging.getLogger("tools.mobile.adb")


def _apk_size_head(path: Path) -> tuple[int, bytes]:
    """The file's size and first bytes, from ONE open handle (raises ``OSError``)."""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(0)
        return size, handle.read(len(APK_MAGIC))


# A host-side sanity gate (right suffix, bounded size, starts like a ZIP), not
# an integrity proof. The file is opened ONCE and its size and first bytes
# come from that one handle, so there is no stat-then-open gap inside the
# check. A missing file or a directory named ``x.apk`` is a reason, not an
# exception. Callers check ``is_file()`` first, and need to: a FIFO would block
# the open, and a NUL in the path raises ``ValueError``.
def apk_problem(path: Path) -> str:
    """Why ``path`` must not be installed, in plain words; ``""`` when it is fine."""
    path = Path(str(path))
    if path.suffix.lower() != ".apk":
        return "the file does not end in .apk"
    try:
        size, head = _apk_size_head(path)
    except OSError as exc:
        return "the file could not be read (" + type(exc).__name__ + ")"
    if size > host().MAX_APK_BYTES:
        return (
            "the file is larger than the "
            + str(host().MAX_APK_BYTES // (1024 * 1024))
            + " MiB limit"
        )
    if head != APK_MAGIC:
        return "the file does not start like an APK (an APK is a ZIP archive)"
    return ""


def _not_installing(path: Path, problem: str) -> dict:
    return {
        "error": (
            "Not installing "
            + str(path)[:200]
            + ": "
            + problem
            + ". Nothing was installed."
        ),
        "content": None,
    }


def _no_apk(path: Path) -> dict:
    return {
        "error": "No APK at " + str(path) + ". Nothing was installed.",
        "content": None,
    }


def _install_outcome(payload: dict, path: Path) -> dict:
    text = str(payload.get("out") or "") + str(payload.get("err") or "")
    if int(payload.get("rc") or 0) != 0 or "Success" not in text:
        failure = parse_failure(text)
        return {"error": failure.message, "content": None, "code": failure.code}
    return {"error": None, "content": {"path": str(path)}}


# Reads NO switch: the mobile lane ships on with no flag, so this function
# gates nothing and the only refusal here is a missing file. Installing an
# app onto a tester's device is one of the effects the contract names, and
# it outlives this call, so whatever consent a caller owes the tester is
# that caller's to collect before it gets here.
async def install(serial: str, apk_path: str) -> dict:
    """``adb install -r -g <apk>`` for a local file that must already exist."""
    try:
        path = Path(str(apk_path)).expanduser()
        if not path.is_file():
            return _no_apk(path)
        problem = host().apk_problem(path)
        if problem:
            return _not_installing(path, problem)
        result = await host().device(
            serial, ["install", "-r", "-g", str(path)], timeout=300
        )
        if result.get("error"):
            return result
        return _install_outcome(result["content"] or {}, path)
    except Exception as exc:
        logger.exception("mobile.adb.install failed")
        return {"error": str(exc), "content": None}


async def uninstall(serial: str, package: str) -> dict:
    """Remove a package, refusing a name that is not a valid package id.

    An uninstall is the one adb effect that repeating cannot undo. Like
    :func:`install` it reads no switch, so the package-name validation is the
    only refusal made here.
    """
    if not valid_package_name(package):
        return {
            "error": "Refusing to uninstall " + repr(str(package)[:60]),
            "content": None,
        }
    return await host().device(serial, ["uninstall", str(package)], timeout=120)
