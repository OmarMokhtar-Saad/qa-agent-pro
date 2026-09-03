"""One emulator, one run at a time: a pid+time lock file.

Deliberately NOT a threading or asyncio lock. The thing being serialised is a
physical emulator shared by every chat on the machine, and the contenders are
separate OS processes (the MCP server, the detached provisioner, a second
editor). A lock that only exists inside one interpreter would not see them.

Staleness has two independent tests and either one releases the lock: the
holder's pid is gone, or the record is older than the caller's ``stale_s``. The
pid test is what handles the common case -- a crashed server -- without waiting
out a timeout; the age test is what handles a pid that has been recycled.

``now`` is injectable everywhere, so the tests assert state transitions instead
of sleeping.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from tools.mobile import paths

logger = logging.getLogger(__name__)

#: Default age after which a lock is considered abandoned.
DEFAULT_STALE_S = 900

#: The lock the whole mobile lane takes.
EMULATOR_LOCK = "emulator"

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _now(value: float | None) -> float:
    return float(value) if value is not None else time.time()


def lock_path(name: str) -> Path:
    return paths.sub("locks") / (str(name) + ".lock")


def _pid_alive(pid: int) -> bool:
    """True when a process with *pid* exists.

    ``os.kill(pid, 0)`` is the portable-enough probe: on POSIX it raises
    ``ProcessLookupError`` for a dead pid and ``PermissionError`` for a live one
    owned by somebody else (which still means ALIVE, so it must not be treated
    as free). CPython implements signal 0 on Windows too.
    """
    try:
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # Unknown -- err towards "alive", because wrongly stealing a lock costs
        # a corrupted run and wrongly keeping it costs one message.
        return True


def _read(name: str) -> dict | None:
    try:
        target = lock_path(name)
        if not target.is_file():
            return None
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else None
    except (OSError, ValueError):
        return None


def _write(name: str, payload: dict) -> None:
    target = lock_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)


def holder(name: str = EMULATOR_LOCK, *, now: float | None = None) -> dict:
    """``{held, pid, owner, age, stale}`` for a lock, without taking it."""
    try:
        body = _read(name)
        if not body:
            return {
                "error": None,
                "content": {
                    "held": False,
                    "pid": 0,
                    "owner": "",
                    "age": 0.0,
                    "stale": False,
                },
            }
        pid = int(body.get("pid") or 0)
        age = _now(now) - float(body.get("time") or 0)
        stale = (not _pid_alive(pid)) or age > DEFAULT_STALE_S
        return {
            "error": None,
            "content": {
                "held": True,
                "pid": pid,
                "owner": str(body.get("owner") or ""),
                "age": age,
                "stale": stale,
            },
        }
    except Exception as exc:
        logger.exception("mobile.locks.holder failed")
        return {"error": str(exc), "content": None}


def acquire(
    name: str = EMULATOR_LOCK,
    *,
    owner: str = "",
    stale_s: int = DEFAULT_STALE_S,
    now: float | None = None,
) -> dict:
    """Take the lock, or refuse naming who holds it.

    ``{"error", "content": {"acquired", "pid", "owner", "stole"}}``. The refusal
    is a CONTENT value rather than an error: "somebody else is using the
    emulator" is a normal state a handler renders, not a fault.
    """
    try:
        if not _NAME_RE.match(str(name or "")):
            return {
                "error": "Refusing " + repr(str(name)[:40]) + " as a lock name.",
                "content": None,
            }
        paths.ensure_tree()
        moment = _now(now)
        payload = {
            "pid": os.getpid(),
            "owner": str(owner or "")[:120],
            "time": moment,
        }
        target = lock_path(name)
        try:
            handle = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            existing = _read(name) or {}
            pid = int(existing.get("pid") or 0)
            age = moment - float(existing.get("time") or 0)
            if pid == os.getpid():
                _write(name, payload)
                return {
                    "error": None,
                    "content": {
                        "acquired": True,
                        "pid": pid,
                        "owner": payload["owner"],
                        "stole": False,
                    },
                }
            if _pid_alive(pid) and age <= max(1, int(stale_s)):
                return {
                    "error": None,
                    "content": {
                        "acquired": False,
                        "pid": pid,
                        "owner": str(existing.get("owner") or ""),
                        "stole": False,
                    },
                }
            logger.info(
                "mobile.locks: stealing %s from pid %s (age %.0fs, alive=%s)",
                name,
                pid,
                age,
                _pid_alive(pid),
            )
            _write(name, payload)
            return {
                "error": None,
                "content": {
                    "acquired": True,
                    "pid": os.getpid(),
                    "owner": payload["owner"],
                    "stole": True,
                },
            }
        else:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, sort_keys=True))
            return {
                "error": None,
                "content": {
                    "acquired": True,
                    "pid": os.getpid(),
                    "owner": payload["owner"],
                    "stole": False,
                },
            }
    except Exception as exc:
        logger.exception("mobile.locks.acquire failed")
        return {"error": str(exc), "content": None}


def release(name: str = EMULATOR_LOCK, *, force: bool = False) -> dict:
    """Release a lock this process holds (or any lock when ``force``)."""
    try:
        body = _read(name)
        if not body:
            return {"error": None, "content": {"released": False, "reason": "not held"}}
        if not force and int(body.get("pid") or 0) != os.getpid():
            return {
                "error": None,
                "content": {"released": False, "reason": "held by another process"},
            }
        lock_path(name).unlink(missing_ok=True)
        return {"error": None, "content": {"released": True, "reason": ""}}
    except Exception as exc:
        logger.exception("mobile.locks.release failed")
        return {"error": str(exc), "content": None}
