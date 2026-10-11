"""The device lock helpers, split out of the :mod:`tools.mobile.session_core` family.

Pure move: ``session`` re-exports every name here, so
``session.take_device_lock is device_lock.take_device_lock`` and existing callers
and patches keep working.
"""

from __future__ import annotations

import logging

from tools.mobile import locks, run_store

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


def take_device_lock(owner: str, *, serial: str = "", lease: str = "") -> dict:
    """Hold ONE DEVICE. A refusal is CONTENT, not an error.

    *owner* is a ``run_id`` once there is one, and a label from
    :func:`new_provisioning_owner` before that. Acquisition is idempotent for
    the same owner in the same process -- it performs no syscall at all -- which
    is what lets every device-touching entry take the lock without counting who
    took it first.

    ``serial`` DEFAULTS TO THE RUN'S OWN DEVICE, and that default is the design
    rather than a convenience. Every caller that has a ``run_id`` already has
    the answer on disk; making each of them look it up would put four copies of
    "which device is this run on" in the handlers, and mirrored derivations
    drift. The pre-run phase, which has no run yet, is the one caller that names
    a serial explicitly.

    A serial that names no device is REFUSED BY NAME rather than falling back to
    a lane-wide lock: a fallback would silently re-serialise every device on the
    machine, which is the behaviour this change exists to remove, and it would
    do it invisibly.
    """
    label = str(owner or "")
    wanted = str(serial or "") or serial_of(label)
    name = locks.device_lock_name(wanted)
    if not name:
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
    took = locks.acquire(name, owner=label, lease=str(lease or ""))
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
                locks.release(other, owner=label, force=True)
    return took


def release_device_lock(
    owner: str, *, lease: str = "", as_holder: bool = False, force: bool = False
) -> dict:
    """Give the emulator back. THE HOLDER releases; there is no reaper anywhere.

    Releasing is safe to call speculatively: a lock this process does not hold,
    or holds under a different owner, is reported as ``released: False`` rather
    than taken from whoever does hold it.

    **AUTHORITY IS OPT-IN, and this default used to be the other way round.**
    ``as_holder=True`` means "I have just claimed this run's lease, so I am its
    authority" -- and while it defaulted to True, a bare call asserted that on
    the caller's behalf. The two DISPLACED branches call this at the exact point
    the handler has been told it LOST the lease, so they asserted authority they
    had just been refused, and a non-holder released the lock the CURRENT holder
    was driving under. Found by an executing release-gate review, demonstrated
    end to end through both handlers, and guarded by nothing.

    So every caller now says which authority it has:

    * a chat that holds (or held) the run's lease passes ``lease=`` and is
      checked against the lock's recorded lease;
    * the pre-run phase passes ``as_holder=True`` for the placeholder it minted
      itself, which has no lease to present.
    """
    label = str(owner or "")
    names = locks.names_held_by(label)
    if not names:
        return {"error": None, "content": {"released": False, "reason": "not held"}}
    released, refusal = _release_names(names, label, lease, as_holder, force)
    return {
        "error": None,
        "content": {"released": released, "reason": "" if released else refusal},
    }


def _release_names(
    names: list, label: str, lease: str, as_holder: bool, force: bool
) -> tuple[bool, str]:
    """Release every lock *label* holds; ``(any released, first refusal reason)``."""
    # BY OWNER, NOT BY NAME. The caller does not always know the serial -- the
    # displaced branches know only that they lost the run -- and asking them to
    # derive one would be a second producer of "which device is this run on".
    # The owner label already identifies exactly one holder, which is the
    # invariant `new_provisioning_owner` exists to keep true, so "everything
    # this label holds" is precisely this caller's own holds and nobody else's.
    released = False
    refusal = ""
    for name in names:
        body = (
            locks.release(
                name,
                owner=label,
                lease=str(lease or ""),
                as_holder=bool(as_holder) and not lease,
                force=bool(force),
            )
            or {}
        ).get("content") or {}
        if body.get("released"):
            released = True
        elif not refusal:
            refusal = str(body.get("reason") or "")
    return released, refusal


def relabel_device_lock(from_owner: str, to_owner: str) -> dict:
    """Hand the device from the provisioning phase to the run that now owns it.

    The SAME file descriptor keeps the SAME kernel lock. Deliberately not
    release-then-reacquire, which would open a window in which another process
    could take the device between the two.
    """
    old = str(from_owner or "")
    # THE SELECT LOCK IS NEVER HANDED TO A RUN. It is the machine's, not the
    # run's, and a run that inherited it would serialise every other device on
    # the machine for its whole life. Releasing it here rather than trusting the
    # caller to have done it keeps the hand-off total: after this call the
    # pre-run label holds nothing, whatever path reached it.
    if locks.EMULATOR_LOCK in locks.names_held_by(old):
        locks.release(locks.EMULATOR_LOCK, owner=old, as_holder=True)
    names = [n for n in locks.names_held_by(old) if n != locks.EMULATOR_LOCK]
    if not names:
        return {
            "error": None,
            "content": {"relabelled": False, "reason": "not held", "holder": ""},
        }
    new = str(to_owner)
    done: list[str] = []
    for name in names:
        moved = locks.relabel(name, from_owner=old, to_owner=new)
        if not ((moved or {}).get("content") or {}).get("relabelled"):
            # ALL OR NOTHING. A half hand-off leaves some locks under the run
            # and some under the pre-run label, and the caller's `finally`
            # releases only the latter -- the rest would be held by a run that
            # believes it owns nothing. Put the moved ones back; if one cannot
            # be put back, release it rather than leave a run-owned hold.
            for back in done:
                undone = locks.relabel(back, from_owner=new, to_owner=old)
                if not ((undone or {}).get("content") or {}).get("relabelled"):
                    locks.release(back, owner=new, as_holder=True)
            return moved
        done.append(name)
    return {"error": None, "content": {"relabelled": True, "reason": "", "holder": ""}}
