"""The qa_mirror_hold backend: one device lock held for the desktop Mirror screen."""

from __future__ import annotations

import json
import logging
import uuid

from handlers import _lane

logger = logging.getLogger("tools.mcp_handlers")


# --------------------------------------------------------------------------- #
# Mirror hold (qa_mirror_hold)
#
# The desktop Mirror screen holds ONE device's lock for as long as it mirrors,
# so a test run cannot start driving a phone somebody is watching. The owner is
# `mirror-<token>`, one token per server process: only a hold made by the
# Mirror screen counts as the app's own, and a run's `mrun-...` hold on the same
# lock stays foreign to it (control and scrcpy stay blocked while a test holds
# the phone). The lock is a file lock held by this process, so it drops when the
# process exits -- the server exits when its stdin closes.
#
# No device command is sent and nothing here raises: every path returns JSON.

_MIRROR_OWNER_PREFIX = "mirror-"
_MIRROR_HOLD_OWNER = _MIRROR_OWNER_PREFIX + uuid.uuid4().hex[:12]
_MIRROR_HOLD_ACTIONS = ("acquire", "release", "status")


def _mirror_hold_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True)


def _mirror_hold_acquire(mobile_locks, name: str, base: dict) -> bool:
    """Take the lock; False when *base* already holds the final refusal."""
    res = mobile_locks.acquire(name, mobile_locks.Claim(owner=_MIRROR_HOLD_OWNER))
    body = res.get("content")
    if res.get("error") or body is None:
        base["reason"] = str(res.get("error") or "the lock could not be taken")
        return False
    if not body.get("acquired"):
        base["reason"] = str(body.get("reason") or "held")
        base["owner"] = str(body.get("holder") or "")
        return False
    return True


def _mirror_hold_release(mobile_locks, name: str, base: dict) -> bool:
    """Release the lock; False when *base* already holds the final answer."""
    res = mobile_locks.release(
        name, mobile_locks.Release(owner=_MIRROR_HOLD_OWNER, as_holder=True)
    )
    body = res.get("content")
    if res.get("error") or body is None:
        base["reason"] = str(res.get("error") or "the lock could not be released")
        return False
    base["reason"] = str(body.get("reason") or "")
    if not body.get("released") and str(base["reason"]).startswith("held by "):
        base["owner"] = str(base["reason"])[len("held by ") :]
        return False
    return True


def _mirror_hold_probe(mobile_locks, name: str, base: dict) -> None:
    """Read the lock's state into *base* (ok, owner, mine, mirror_hold, reason)."""
    probed = mobile_locks.holder(name)
    info = probed.get("content")
    if probed.get("error") or info is None:
        base["reason"] = str(probed.get("error") or "the lock could not be read")
        return
    held = bool(info.get("held"))
    owner = str(info.get("owner") or "") if held else ""
    mine = held and bool(info.get("mine"))
    base.update(
        ok=True,
        owner=owner,
        mine=mine,
        mirror_hold=mine and owner.startswith(_MIRROR_OWNER_PREFIX),
        reason=base["reason"] or ("held" if held else "free"),
    )


def _mirror_base(action: str, serial: str) -> dict:
    """The reply object every path fills in and returns."""
    return {
        "ok": False,
        "action": str(action or "").strip().lower(),
        "serial": str(serial or "").strip(),
        "owner": "",
        "mine": False,
        "mirror_hold": False,
        "reason": "",
    }


def _mirror_step(mobile_locks, name: str, base: dict) -> bool:
    """Run the acquire / release step; False when *base* is already final."""
    what = base["action"]
    if what == "acquire":
        return _mirror_hold_acquire(mobile_locks, name, base)
    if what == "release":
        return _mirror_hold_release(mobile_locks, name, base)
    return True


def _mirror_run(base: dict) -> dict:
    """Validate, take the requested step, then read the lock state into *base*."""
    mobile_locks = _lane.locks
    what = base["action"]
    if what not in _MIRROR_HOLD_ACTIONS:
        base["reason"] = "action must be one of: " + ", ".join(_MIRROR_HOLD_ACTIONS)
        return base
    name = mobile_locks.device_lock_name(base["serial"])
    if not name:
        base["reason"] = "that is not a device serial"
        return base
    if _mirror_step(mobile_locks, name, base):
        _mirror_hold_probe(mobile_locks, name, base)
    return base


def handle_mirror_hold(serial: str = "", action: str = "status") -> str:
    """``qa_mirror_hold``: acquire, release or read ONE device's lock.

    Always a JSON object with ``ok``, ``action``, ``serial``, ``owner``,
    ``mine`` and ``mirror_hold``. ``mirror_hold`` is true only when the lock is
    held by THIS process (``mine``) AND its owner starts with ``mirror-``; it is
    computed here so the Kotlin side never reads ``mine`` on its own.
    """
    base = _mirror_base(action, serial)
    try:
        return _mirror_hold_json(_mirror_run(base))
    except Exception as exc:
        logger.exception("handle_mirror_hold failed")
        base["ok"] = False
        base["reason"] = str(exc)[:200]
        return _mirror_hold_json(base)
