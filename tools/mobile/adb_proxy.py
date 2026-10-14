"""Proxy primitives: reverse/forward and global settings (moved from adb.py).

Four small transport calls `tools/mobile_capture/proxy.py` and
`tools/mobile_capture/teardown.py` build on. Every argument is
re-validated HERE, the same discipline every other function in the adb
modules keeps, so a client-controlled string can never smuggle an extra `adb`
argument regardless of what called this module.
"""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile.adb_consts import DEFAULT_TIMEOUT_S
from tools.mobile.adb_host import host


class PortSpec(NamedTuple):
    """The two sides of a reverse/forward spec, plus the adb timeout."""

    remote: str
    local: str
    timeout: int = DEFAULT_TIMEOUT_S


class SettingPut(NamedTuple):
    """A global setting name and value to write, plus the adb timeout."""

    name: str
    value: str
    timeout: int = DEFAULT_TIMEOUT_S


def _refuse(what: str, value: object, width: int) -> dict:
    return {
        "error": "Refusing " + what + " " + repr(str(value)[:width]),
        "content": None,
    }


async def reverse(serial: str, spec: PortSpec) -> dict:
    """``adb -s <serial> reverse <remote> <local>``.

    Both sides must be ``tcp:<port>`` -- the only spec this lane ever sends,
    since the port on the device side and the port qa-agents' own proxy
    listens on are both chosen by :mod:`tools.mobile_capture.proxy`, never
    taken from anything device-supplied.

    *timeout* is a plain pass-through to :func:`device`, so a caller with its
    own bound (``tools/mobile_capture/teardown.py``'s ``CLEAR_TIMEOUT_S``) is
    not stuck with this module's general-purpose default.
    """
    for what, side in (("remote", spec.remote), ("local", spec.local)):
        if not host()._REVERSE_SPEC_RE.match(str(side or "")):
            return _refuse("reverse " + what, side, 40)
    return await host().device(
        serial, ["reverse", str(spec.remote), str(spec.local)], spec.timeout
    )


async def reverse_remove(
    serial: str, remote: str, timeout: int = DEFAULT_TIMEOUT_S
) -> dict:
    """``adb -s <serial> reverse --remove <remote>``. See :func:`reverse` on *timeout*."""
    if not host()._REVERSE_SPEC_RE.match(str(remote or "")):
        return _refuse("reverse remote", remote, 40)
    return await host().device(serial, ["reverse", "--remove", str(remote)], timeout)


def _forward_failure(body: dict) -> dict:
    text = " ".join(str(body.get("err") or body.get("out") or "").split())
    detail = text[:200] or "exit code " + str(body["rc"])
    return {"error": "adb forward failed: " + detail, "content": None}


async def forward(serial: str, spec: PortSpec) -> dict:
    """``adb -s <serial> forward <local> <remote>``.

    Both sides must be ``tcp:<port>`` (same spec as :func:`reverse`). ``raw``
    reports ``error`` None even when adb exits non-zero, and a refused forward
    exits non-zero, so a non-zero exit is turned into an ``error`` here: the
    caller only reads ``error``. See :func:`reverse` on *timeout*.
    """
    for what, side in (("local", spec.local), ("remote", spec.remote)):
        if not host()._REVERSE_SPEC_RE.fullmatch(str(side or "")):
            return _refuse("forward " + what, side, 40)
    got = await host().device(
        serial, ["forward", str(spec.local), str(spec.remote)], spec.timeout
    )
    body = got.get("content")
    if got.get("error") or not isinstance(body, dict) or not body.get("rc"):
        return got
    return _forward_failure(body)


async def global_setting_get(
    serial: str, name: str, timeout: int = DEFAULT_TIMEOUT_S
) -> dict:
    """``adb -s <serial> shell settings get global <name>``, stripped.

    See :func:`reverse` on *timeout*.
    """
    if not host()._SETTING_NAME_RE.match(str(name or "")):
        return _refuse("setting name", name, 40)
    result = await host().shell(
        serial, ["settings", "get", "global", str(name)], timeout
    )
    if result.get("error"):
        return result
    return {
        "error": None,
        "content": str((result["content"] or {}).get("out") or "").strip(),
    }


async def global_setting_put(serial: str, setting: SettingPut) -> dict:
    """``adb -s <serial> shell settings put global <name> <value>``.

    This is the setter the teardown invariant
    (``tools/mobile_capture/teardown.py``) depends on: it must REFUSE rather
    than silently drop a value the caller intended, because a refused ``:0``
    write is exactly the shape of bug that leaves a device proxied. See
    :func:`reverse` on *timeout*.
    """
    if not host()._SETTING_NAME_RE.match(str(setting.name or "")):
        return _refuse("setting name", setting.name, 40)
    text = str(setting.value or "")
    if not host()._SETTING_VALUE_RE.match(text):
        return _refuse("setting value", text, 60)
    args = ["settings", "put", "global", str(setting.name), text]
    return await host().shell(serial, args, setting.timeout)
