"""The device lock helpers, split out of the :mod:`tools.mobile.session_core` family.

Pure move: ``session`` re-exports every name here, so
``session.take_device_lock is device_lock.take_device_lock`` and existing callers
and patches keep working.
"""

from __future__ import annotations

import logging

from tools.mobile import device_lock_heal, locks, run_store
from tools.mobile.device_lock_hold import DeviceRelease, _release_names  # noqa: F401
from tools.mobile.device_lock_relabel import _move_all, _roll_back  # noqa: F401

# kept for log-filter compatibility: records keep the logger name they had in session
logger = logging.getLogger("tools.mobile.session")


#: Re-exported so a handler never imports ``locks`` itself: the pre-run phase
#: has no ``run_id`` to own the device with, so it holds the lock under this
#: per-process label and :func:`relabel_device_lock` hands it to the run.
def new_provisioning_owner() -> str:
    """A fresh pre-run owner label. See :func:`locks.new_provisioning_owner`.

    Re-exported as a FUNCTION so a handler never imports ``locks`` itself and
    cannot hold a shared label by mistake: the pre-run phase has no ``run_id``
    to own the device with, so it holds under one of these until
    :func:`relabel_device_lock` hands the lock to the run.
    """
    return locks.new_provisioning_owner()


def serial_of(run_id: str) -> str:
    """The device a run is on, from its manifest. ``""`` when it has none.

    THE ONE PRODUCER of that fact for the locking path. Every other route to it
    -- a resolved body, a packet, a context -- reads the same manifest field, so
    this is not a second source; it is the one call the lock helpers may use
    without dragging a whole ``resolve`` through the pre-packet path.
    """
    try:
        manifest = (run_store.read_manifest(str(run_id)) or {}).get("content") or {}
        return str(manifest.get("serial") or "")
    except Exception:  # pragma: no cover - defensive; a status is not a verdict
        logger.exception("mobile.session.serial_of failed")
        return ""


# ``serial`` DEFAULTS TO THE RUN'S OWN DEVICE, and that default is the design
# rather than a convenience. Every caller that has a ``run_id`` already has
# the answer on disk; making each of them look it up would put four copies of
# "which device is this run on" in the handlers, and mirrored derivations
# drift. The pre-run phase, which has no run yet, is the one caller that names
# a serial explicitly.
def _serial_for(label: str, serial: str) -> str:
    """The device lock name for *serial*, defaulting to the run's own device."""
    return locks.device_lock_name(str(serial or "") or serial_of(label))


# A serial that names no device is REFUSED BY NAME rather than falling back to
# a lane-wide lock: a fallback would silently re-serialise every device on the
# machine, which is the behaviour this change exists to remove, and it would
# do it invisibly.
def _no_device(label: str) -> dict:
    """The refusal for a serial that names no device."""
    return {
        "error": None,
        "content": {
            "acquired": False,
            "owner": label,
            "holder": "",
            "reentrant": False,
            "same_process": False,
            "reason": "no_device",
        },
    }


def _take_checked(label: str, name: str, lease: str) -> dict:
    """Acquire *name* for *label*, then drop any other device it holds."""
    took = locks.acquire(name, locks.Claim(owner=label, lease=str(lease or "")))
    if ((took or {}).get("content") or {}).get("acquired"):
        # ONE DEVICE PER OWNER, enforced at the moment a device is taken. An
        # emulator that reboots comes back on a different serial, so a resumed
        # run can otherwise end up holding the lock of a device it is no longer
        # driving -- and nothing would ever release it, because every release
        # route asks what this owner holds and would find two answers. The
        # SELECT lock is deliberately excluded: the pre-run phase holds it
        # alongside the device it has just taken, for the one line it takes to
        # give it back.
        for other in locks.names_held_by(label):
            if other not in (name, locks.EMULATOR_LOCK):
                locks.release(other, locks.Release(owner=label, force=True))
    return took


def take_device_lock(owner: str, *, serial: str = "", lease: str = "") -> dict:
    """Hold ONE DEVICE. A refusal is CONTENT, not an error.

    *owner* is a ``run_id`` once there is one, and a label from
    :func:`new_provisioning_owner` before that. Acquisition is idempotent for
    the same owner in the same process -- it performs no syscall at all -- which
    is what lets every device-touching entry take the lock without counting who
    took it first.
    """
    label = str(owner or "")
    name = _serial_for(label, serial)
    if not name:
        return _no_device(label)
    took = _take_checked(label, name, lease)
    # A same-process pre-run hold whose setup call has ENDED can never be
    # released by anyone else (fix/1520): heal it once, then retake once.
    orphan = device_lock_heal.orphan_holder(took, label)
    if orphan and device_lock_heal.release_orphan(name, orphan):
        took = _take_checked(label, name, lease)
    return took


def release_device_lock(owner: str, opts: DeviceRelease | None = None) -> dict:
    """Give the emulator back. THE HOLDER releases; there is no reaper anywhere.

    Releasing is safe to call speculatively: a lock this process does not hold,
    or holds under a different owner, is reported as ``released: False`` rather
    than taken from whoever does hold it. Authority is opt-in: see
    :class:`DeviceRelease`.
    """
    label = str(owner or "")
    names = locks.names_held_by(label)
    if not names:
        return {"error": None, "content": {"released": False, "reason": "not held"}}
    released, refusal = _release_names(names, label, opts or DeviceRelease())
    return {
        "error": None,
        "content": {"released": released, "reason": "" if released else refusal},
    }


# THE SELECT LOCK IS NEVER HANDED TO A RUN. It is the machine's, not the
# run's, and a run that inherited it would serialise every other device on
# the machine for its whole life. Releasing it here rather than trusting the
# caller to have done it keeps the hand-off total: after this call the
# pre-run label holds nothing, whatever path reached it.
def _drop_select_lock(old: str) -> None:
    """Release the machine-wide select lock *old* holds; it is never handed on."""
    if locks.EMULATOR_LOCK in locks.names_held_by(old):
        locks.release(locks.EMULATOR_LOCK, locks.Release(owner=old, as_holder=True))


def relabel_device_lock(from_owner: str, to_owner: str) -> dict:
    """Hand the device from the provisioning phase to the run that now owns it.

    The SAME file descriptor keeps the SAME kernel lock. Deliberately not
    release-then-reacquire, which would open a window in which another process
    could take the device between the two.
    """
    old = str(from_owner or "")
    _drop_select_lock(old)
    names = [n for n in locks.names_held_by(old) if n != locks.EMULATOR_LOCK]
    if not names:
        return {
            "error": None,
            "content": {"relabelled": False, "reason": "not held", "holder": ""},
        }
    refused = _move_all(names, old, str(to_owner))
    if refused is not None:
        return refused
    return {"error": None, "content": {"relabelled": True, "reason": "", "holder": ""}}
