"""``run-as`` reads of an app's private files: run_as_ls, run_as_cat (moved from adb.py)."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile.adb_consts import MAX_DUMP_BYTES
from tools.mobile.adb_host import host


def _run_as_refused(rc: int, out: str, err: str) -> str | None:
    text = (out + "\n" + err).strip()
    if rc != 0 or "run-as:" in text.lower():
        return text[:300] or ("run-as exited " + str(rc))
    return None


def _precheck(package: str, path: str) -> dict | None:
    """The refusal for a package or path that fails validation, else None."""
    if not host().valid_package_name(package):
        return {
            "error": "Refusing run-as for " + repr(str(package)[:60]),
            "content": None,
        }
    if not host()._valid_device_path(path):
        return {
            "error": "Refusing device path " + repr(str(path)[:80]),
            "content": None,
        }
    return None


def _failure(result: dict) -> dict | None:
    """The error reply when the call failed or run-as refused, else None."""
    if result.get("error"):
        return result
    body = result["content"] or {}
    refused = host()._run_as_refused(
        int(body.get("rc") or 0), str(body.get("out") or ""), str(body.get("err") or "")
    )
    return None if refused is None else {"error": refused, "content": None}


async def run_as_ls(serial: str, package: str, path: str) -> dict:
    """``run-as <package> ls -1 <path>``: the entry names, or the refusal as error.

    Only the run's own *package* may be named; *path* must be absolute with no
    ``..``. A release (non-debuggable) build makes ``run-as`` refuse, and that
    refusal is the error text so the caller can state it.
    """
    bad = _precheck(package, path)
    if bad is not None:
        return bad
    result = await host().shell(serial, ["run-as", str(package), "ls", "-1", str(path)])
    failed = _failure(result)
    if failed is not None:
        return failed
    out = str((result["content"] or {}).get("out") or "")
    names = [
        line.strip().rsplit("/", 1)[-1] for line in out.splitlines() if line.strip()
    ]
    return {"error": None, "content": names}


class RunAsRead(NamedTuple):
    package: str
    path: str
    max_bytes: int = MAX_DUMP_BYTES


# ``exec-out`` rather than ``shell`` because ``shell`` is a pty and mangles line
# endings. ``{"error", "content": {"data", "truncated"}}``; the data is never
# logged.
async def run_as_cat(serial: str, read: RunAsRead) -> dict:
    """``exec-out run-as <package> cat <path>``, byte-capped."""
    bad = _precheck(read.package, read.path)
    if bad is not None:
        return bad
    result = await host().device(
        serial,
        ["exec-out", "run-as", str(read.package), "cat", str(read.path)],
        timeout=host().DUMP_TIMEOUT_S,
    )
    failed = _failure(result)
    if failed is not None:
        return failed
    out = str((result["content"] or {}).get("out") or "")
    data, truncated = host()._capped(out, read.max_bytes)
    return {"error": None, "content": {"data": data, "truncated": truncated}}
