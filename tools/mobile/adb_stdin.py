"""Device-scoped adb calls that feed stdin: ``shell_in`` (split from adb_props)."""

from __future__ import annotations

from typing import NamedTuple

from tools.device_ids import _valid_device_id
from tools.mobile.adb_consts import DEFAULT_TIMEOUT_S
from tools.mobile.adb_host import host
from tools.mobile.adb_reply import refuse_device_id


class StdinFeed(NamedTuple):
    """Bytes fed to adb's stdin (``None`` = none) and the call timeout."""

    data: bytes | None = None
    timeout: int = DEFAULT_TIMEOUT_S


def _gone_in(result: dict) -> bool:
    body = result.get("content")
    return bool(
        isinstance(body, dict)
        and body.get("rc")
        and host()._DEVICE_GONE_RE.search(str(body.get("err") or "")[:2000])
    )


async def _device_in(serial: str, args: list[str], feed: StdinFeed) -> dict:
    if not _valid_device_id(serial):
        return refuse_device_id(serial)
    result = await host().raw(["-s", str(serial)] + list(args), feed.timeout, feed.data)
    if _gone_in(result):
        host()._notify_device_gone()
    return result


async def shell_in(serial: str, args: list[str], feed: StdinFeed) -> dict:
    """``adb -s <serial> shell <args>`` with *feed* on stdin.

    Calling this with an EMPTY ``args`` and a ``feed.data`` payload runs adb's
    interactive shell and feeds it the command line on stdin. That is the only
    supported way to send a secret: see ``ime.type_text``.
    """
    return await host()._device_in(serial, ["shell"] + [str(a) for a in args], feed)
