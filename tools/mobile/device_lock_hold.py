"""The release half of the device lock: the options and the by-owner release loop.

Split out of :mod:`tools.mobile.device_lock`; that facade re-exports every name.
Imports :mod:`tools.mobile.locks` only, never ``device_lock``.
"""

from __future__ import annotations

from dataclasses import dataclass

from tools.mobile import locks


# **AUTHORITY IS OPT-IN, and this default used to be the other way round.**
# ``as_holder=True`` means "I have just claimed this run's lease, so I am its
# authority" -- and while it defaulted to True, a bare call asserted that on
# the caller's behalf. The two DISPLACED branches call release at the exact
# point the handler has been told it LOST the lease, so they asserted authority
# they had just been refused, and a non-holder released the lock the CURRENT
# holder was driving under. Found by an executing release-gate review,
# demonstrated end to end through both handlers, and guarded by nothing.
#
# So every caller now says which authority it has:
#
# * a chat that holds (or held) the run's lease passes ``lease=`` and is
#   checked against the lock's recorded lease;
# * the pre-run phase passes ``as_holder=True`` for the placeholder it minted
#   itself, which has no lease to present.
@dataclass(frozen=True)
class DeviceRelease:
    """The authority a ``release_device_lock`` caller presents."""

    lease: str = ""
    as_holder: bool = False
    force: bool = False


# BY OWNER, NOT BY NAME. The caller does not always know the serial -- the
# displaced branches know only that they lost the run -- and asking them to
# derive one would be a second producer of "which device is this run on".
# The owner label already identifies exactly one holder, which is the
# invariant `new_provisioning_owner` exists to keep true, so "everything
# this label holds" is precisely this caller's own holds and nobody else's.
def _release_names(names: list, owner: str, opts: DeviceRelease) -> tuple[bool, str]:
    """Release every lock *owner* holds; ``(any released, first refusal reason)``."""
    released = False
    refusal = ""
    req = locks.Release(
        owner=owner,
        lease=str(opts.lease or ""),
        as_holder=bool(opts.as_holder) and not opts.lease,
        force=bool(opts.force),
    )
    for name in names:
        body = (locks.release(name, req) or {}).get("content") or {}
        if body.get("released"):
            released = True
        elif not refusal:
            refusal = str(body.get("reason") or "")
    return released, refusal
