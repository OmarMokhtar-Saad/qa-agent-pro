"""Enable, select and restore the QA input method."""

from __future__ import annotations

from tools.mobile import adb, ime_cache
from tools.mobile.ime_component import same_component
from tools.mobile.ime_consts import _IME_REFUSAL_RE
from tools.mobile.ime_host import host
from tools.mobile.ime_listing import _list_ime_ids


def _enable_refusal(result: dict) -> dict | None:
    """The error answer when ``ime enable`` ran but failed, else None."""
    body = result.get("content") or {}
    rc = int(body.get("rc") or 0)
    combined = str(body.get("out") or "") + str(body.get("err") or "")
    if rc != 0 or _IME_REFUSAL_RE.search(combined):
        return {
            "error": "adb ime enable failed: " + combined.strip()[:400],
            "content": None,
        }
    return None


# Checks the device's actual answer the same way :func:`current_ime` already
# does in this file: a non-zero ``rc`` OR a refusal phrase in the combined
# stdout/stderr is a failure, since ``ime enable`` can exit 0 while printing a
# refusal.
async def enable(serial: str) -> dict:
    """``ime enable`` for the pinned id (an IME must be enabled before it is set)."""
    resolved = await host().resolve_installed_id(serial)
    if resolved.get("error"):
        return resolved
    ime_id = str(resolved["content"])
    result = await adb.shell(serial, ["ime", "enable", ime_id])
    if result.get("error"):
        return result
    return _enable_refusal(result) or {"error": None, "content": {}}


async def _select_fresh(serial: str, ime_id: str) -> dict:
    """``pm_select`` that drops the per-step keyboard read around the switch."""
    ime_cache.invalidate(serial)
    try:
        return await adb.pm_select(serial, ime_id)
    finally:
        ime_cache.invalidate(serial)


async def select(serial: str) -> dict:
    """Make the pinned IME the active one, by the id the device lists."""
    resolved = await host().resolve_installed_id(serial)
    if resolved.get("error"):
        return resolved
    return await _select_fresh(serial, str(resolved["content"]))


def _noop_restore(target: str, ours: str) -> dict | None:
    """The reported no-op answer when there is nothing to restore, else None."""
    if not target:
        detail = (
            "No previous input method was recorded for this device, so "
            "none was restored."
        )
    elif same_component(target, ours):
        detail = "The QA input method was already the default before this run."
    else:
        return None
    return {
        "error": None,
        "content": {"restored": False, "previous": target, "detail": detail},
    }


async def _restore_target(serial: str, target: str) -> dict:
    """Re-select *target*, in the device's own listed spelling when it has one."""
    # A malformed id is refused by pm_select without a device call;
    # listing first would spend one on a value that cannot be sent.
    listed = await _list_ime_ids(serial) if adb.is_ime_id(target) else []
    send = next((c for c in listed if same_component(c, target)), target)
    return await _select_fresh(serial, send)


# A blank *previous* is a REPORTED no-op: the device had no default input
# method recorded, and inventing one would leave the tester's emulator in a
# state we chose for them. A *previous* that is our own id is also a no-op --
# restoring it would be pointless, and it is exactly the case a test must not
# be allowed to assume.
async def restore_previous(serial: str, previous: str) -> dict:
    """Re-select whatever was active before the run."""
    resolved = host().manifest()
    ours = str((resolved.get("content") or {}).get("ime_id") or "")
    target = str(previous or "").strip()
    noop = _noop_restore(target, ours)
    if noop is not None:
        return noop
    result = await _restore_target(serial, target)
    if result.get("error"):
        return result
    return {
        "error": None,
        "content": {
            "restored": True,
            "previous": target,
            "detail": "restored " + target,
        },
    }
