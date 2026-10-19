"""Shared lock state: names, the in-memory fd table and the parameter objects.

Split out of :mod:`tools.mobile.locks` (the facade); imports nothing from
``tools.mobile``.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
from dataclasses import dataclass

from tools.mobile import run_consts


class LockUnsupported(RuntimeError):
    """Neither locking primitive exists on this platform."""


#: The lock the SELECTION phase takes -- the same file this lane has always
#: used, held for a shorter window.
#:
#: TWO LOCKS, TWO QUESTIONS, and the division is the whole design. Picking a
#: device is a question about the MACHINE: it reads ``adb devices``, may adopt a
#: foreign emulator and may spawn the shared default one, and there is no serial
#: yet to key a lock by -- which is exactly why this constant used to be the
#: lane's only lock. Driving a device afterwards is a question about ONE DEVICE,
#: and that is :func:`device_lock_name`. So the pre-run phase takes this, names
#: a device, takes THAT device's lock, and gives this one back before the long
#: steps (install, preflight, the run itself) begin. Two runs on two devices
#: then overlap everywhere except the seconds of selection; two runs on ONE
#: device still serialise, on the device lock, exactly as they did before.
EMULATOR_LOCK = "emulator"

#: Every device lock name starts here, so one ``startswith`` tells a device lock
#: from :data:`EMULATOR_LOCK` without parsing.
DEVICE_LOCK_PREFIX = "device-"


PROVISIONING_PREFIX = "provisioning:"


def is_provisioning_owner(label: object) -> bool:
    """Whether *label* is a pre-run owner label, not a run id."""
    return str(label or "").startswith(PROVISIONING_PREFIX)


def new_provisioning_owner() -> str:
    """A fresh owner label for the pre-run phase, when no ``run_id`` exists yet.

    A FUNCTION, not a constant, and that is the whole point.

    AN OWNER LABEL MUST IDENTIFY EXACTLY ONE HOLDER. This used to be the
    module-level constant ``"provisioning:" + str(os.getpid())`` -- one label
    per PROCESS -- and :func:`acquire` grants a matching label reentrantly. A
    label two callers can both present is not an owner, so reentrancy on it was
    not reentrancy: it was SHARING. Two concurrent new-run calls in one MCP
    server both got ``acquired=True`` and both drove the same emulator, one
    caller's release freed the other's hold, and the loser's relabel then failed
    -- three defects, one shared label. Found by an executing review, on the
    primary path, with no stub.

    So each call mints its own. The label still names the process, because that
    is what a refusal message needs to say, but the random half is what makes it
    an identity.
    """
    return "%s%d:%s" % (PROVISIONING_PREFIX, os.getpid(), secrets.token_hex(4))


#: The ``runs/`` folder prefix of a pre-run owner's records (see :func:`record_segment`).
PRERUN_DIR_PREFIX = run_consts.PRERUN_DIR_PREFIX


def record_segment(owner: object) -> str:
    """The ``runs/`` folder name for *owner*'s records: a run id as is, a pre-run
    owner made path-safe (its ``:`` is not a path character ``run_dir`` accepts)."""
    label = str(owner or "")
    if not is_provisioning_owner(label):
        return label
    return PRERUN_DIR_PREFIX + label[len(PROVISIONING_PREFIX) :].replace(":", "-")


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


#: Guards every read and mutation of :data:`_HELD`, across the WHOLE body of
#: each public function -- the window that matters is between the ``flock``
#: succeeding and ``_HELD`` recording it, not the dict write alone. ``RLock`` so
#: that one of these functions calling another cannot self-deadlock.
_MUTEX = threading.RLock()


@dataclass
class _Held:
    """One lock this process holds. The fd IS the ownership.

    ``lease`` is the run lease the lock was taken UNDER, and it exists for one
    reason: an owner label must identify exactly one holder, and a ``run_id``
    does not. Every chat that ever claims a run presents the same run id, so a
    leftover heartbeat writer for that run -- one whose lease was revoked chats
    ago -- would otherwise release the lock the CURRENT lease holder is using,
    handing the device to a third process mid-run. Measured before this field
    existed.

    It is deliberately NOT the owner label and is never rendered: the
    tester-facing owner stays the run id, because that is what a refusal has to
    be able to name and what a takeover instruction has to be able to use.
    """

    fd: int
    owner: str
    since: float
    lease: str = ""


_HELD: dict[str, _Held] = {}


def _refuse_name(name: object) -> dict | None:
    """The refusal dict for a bad lock name, or ``None`` when it is fine.

    ENFORCED AT EACH PUBLIC BOUNDARY, not only inside :func:`lock_path`. Putting
    the check solely in `lock_path` and trusting it to propagate made it INERT
    for :func:`holder`: `_read` catches ``(OSError, ValueError)`` and answers
    ``None``, `_probe_free` catches ``Exception`` and answers "busy", so
    ``holder("../escape")`` came back ``{"held": True}`` with no error at all.
    Two conservative handlers, one guard that never fired.
    """
    if not _NAME_RE.match(str(name or "")):
        return {
            "error": "Refusing " + repr(str(name)[:40]) + " as a lock name.",
            "content": None,
        }
    return None


@dataclass(frozen=True)
class Claim:
    """The caller's side of an ``acquire``: who, under which lease, at what time."""

    owner: str = ""
    lease: str = ""
    now: float | None = None


@dataclass(frozen=True)
class Release:
    """The caller's side of a ``release``: who, under which lease, and how hard."""

    owner: str = ""
    lease: str = ""
    as_holder: bool = False
    force: bool = False
