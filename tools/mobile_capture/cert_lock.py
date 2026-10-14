"""The per-serial device lock shared by ``cert.install`` and ``cert.remove``."""

from __future__ import annotations

from tools.mobile import device_lock
from tools.mobile_capture import proxy


def _owner_label(owner: str) -> tuple[str, bool]:
    """``(label, minted)``: the caller's owner, or a freshly minted provisioning one."""
    minted = not str(owner or "").strip()
    label = str(owner or "").strip() or device_lock.new_provisioning_owner()
    return label, minted


def _busy(label: str, serial: str) -> dict | None:
    """The refusal when the device lock is held by someone else, else None."""
    took = (device_lock.take_device_lock(label, serial=serial) or {}).get(
        "content"
    ) or {}
    if took.get("acquired"):
        return None
    return {
        "error": proxy.REASON_DEVICE_BUSY,
        "content": {"holder": str(took.get("holder") or "")},
    }


def _release(label: str, minted: bool) -> None:
    """Release a lock this call minted; a caller-held lock is never released here."""
    if minted:
        device_lock.release_device_lock(
            label, device_lock.DeviceRelease(as_holder=True)
        )


async def _with_lock(serial: str, lock: tuple[str, bool], run) -> dict:
    """Run ``run()`` under the device lock; the busy refusal when it is taken."""
    label, minted = lock
    busy = _busy(label, serial)
    if busy:
        return busy
    try:
        return await run()
    finally:
        _release(label, minted)
