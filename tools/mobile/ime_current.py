"""The device's current input method, and remembering it before we select ours."""

from __future__ import annotations

from tools.mobile import adb
from tools.mobile.ime_component import same_component
from tools.mobile.ime_host import host


def _probe_failure(body: dict, rc: int) -> dict:
    """The error answer for a ``settings get`` that ran and exited non-zero."""
    detail = str(body.get("err") or "").strip() or str(body.get("out") or "").strip()
    return {
        "error": "adb settings get default_input_method failed ("
        + (detail[:200] if detail else "exit " + str(rc))
        + ").",
        "content": None,
    }


# A NON-ZERO EXIT IS A FAILED PROBE, NOT "(none)" -- the rule `adb.devices`
# and `adb.installed_packages` already state. `shell` does not inspect the
# exit code, so a `settings get` that RAN and failed arrived here with
# `error=None` and empty stdout, and preflight rendered
# "active input method: (none)" for a device whose keyboard it never asked.
# The consumer's guard cannot fire on evidence the producer never sets.
async def current_ime(serial: str) -> dict:
    """The device's currently selected input method id, or ``""``."""
    result = await adb.shell(
        serial, ["settings", "get", "secure", "default_input_method"]
    )
    if result.get("error"):
        return result
    body = result.get("content") or {}
    rc = int(body.get("rc") or 0)
    if rc != 0:
        return _probe_failure(body, rc)
    text = str(body.get("out") or "").strip()
    if text.lower() in ("", "null"):
        return {"error": None, "content": ""}
    return {"error": None, "content": text}


# "was_ours" is by component identity, not spelling: the device reports the
# shorthand `pkg/.Class` and the manifest pins the expanded form, so `==`
# answered "that is not our keyboard" about our own keyboard -- and the run
# would then try to 'restore' it as if it were the tester's.
async def remember_previous(serial: str) -> dict:
    """Read the input method to restore later, BEFORE selecting ours.

    ``{"previous": <id or "">, "was_ours": bool}``. On this project's own
    emulator the answer is a third-party keyboard, so nothing downstream may
    treat an unfamiliar value as an error.
    """
    resolved = host().manifest()
    ours = str((resolved.get("content") or {}).get("ime_id") or "")
    current = await host().current_ime(serial)
    if current.get("error"):
        return current
    previous = str(current.get("content") or "")
    return {
        "error": None,
        "content": {"previous": previous, "was_ours": same_component(previous, ours)},
    }
