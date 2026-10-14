"""Device media transport: ``screenrecord_spawn`` and ``pull`` (moved from adb.py)."""

from __future__ import annotations

import asyncio
import logging

from config.settings_guard import never_raise
from tools.mobile import platform_info
from tools.mobile.adb_host import host
from tools.mobile.adb_reply import refuse_device_id

logger = logging.getLogger("tools.mobile.adb")


def _bad_remote() -> dict:
    """The refusal for a remote path that failed validation."""
    return {"error": "Refusing that remote path.", "content": None}


def _record_refusal(serial: str, remote: str) -> dict | None:
    """The refusal for a bad device id or remote path, else ``None``."""
    # Reads NO switch (the lane ships on with no flag). A recording is an
    # effect that OUTLIVES this call -- it leaves a process running on a
    # tester's device and a file on its disk -- so the refusals here, on the
    # device id and the remote path, are all that is guarded.
    if not host()._valid_device_id(serial):
        return refuse_device_id(serial)
    if not host()._valid_device_path(remote):
        return _bad_remote()
    return None


def _record_cmd(serial: str, remote: str, seconds: int) -> list[str]:
    """The ``adb shell screenrecord`` argv for one bounded recording."""
    return [
        host().resolve_adb(),
        "-s",
        str(serial),
        "shell",
        "screenrecord",
        "--time-limit",
        str(int(seconds)),
        "--bit-rate",
        host().SCREENRECORD_BITRATE,
        "--size",
        host().SCREENRECORD_SIZE,
        str(remote),
    ]


async def _spawned(serial: str, remote: str, seconds: int) -> dict:
    refusal = _record_refusal(serial, remote)
    if refusal is not None:
        return refusal
    proc = await asyncio.create_subprocess_exec(
        *_record_cmd(serial, remote, seconds),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **platform_info.no_window_kwargs(),
    )
    return {"error": None, "content": {"proc": proc}}


# The ONE function in this module that hands a process back instead of a
# finished result: a recording outlives the call that starts it by design.
# ``--time-limit`` is passed explicitly rather than inherited from the
# device's own ~180s default, so the bound is a number this repository owns
# and can bound from above.
async def screenrecord_spawn(serial: str, remote: str, seconds: int) -> dict:
    """Start ``screenrecord`` on the device and return the live process."""
    with never_raise(
        logger,
        "mobile.adb.screenrecord_spawn failed: %s",
        level=logging.WARNING,
        with_exc=True,
    ) as g:
        try:
            return await _spawned(serial, remote, seconds)
        except FileNotFoundError:
            return {"error": "adb was not found.", "content": None}
    return {"error": str(g.exc)[:200], "content": None}


async def pull(serial: str, remote: str, local: str) -> dict:
    """``adb -s <serial> pull`` one device file to *local*. Never raises.

    ``device`` takes the SERIAL first and the argv second -- handing it the
    argv alone makes the list itself the thing ``_valid_device_id`` judges, so
    every pull would come back "Refusing to use ... as a device id" and every
    clip would be recorded on the device and then lost. Pinned by
    ``tests/mobile/test_mobile_media.py::test_pull_sends_the_serial_first``,
    which drives THIS function against a fake ``run_argv`` rather than
    replacing it.
    """
    if not host()._valid_device_path(remote):
        return _bad_remote()
    return await host().device(
        serial, ["pull", str(remote), str(local)], host().DUMP_TIMEOUT_S
    )
