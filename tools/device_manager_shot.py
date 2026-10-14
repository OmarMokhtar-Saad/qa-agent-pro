"""Screenshot capture for ``tools.device_manager``."""

from __future__ import annotations

import asyncio
import logging
import os

from tools.device_ids import _valid_device_id
from tools.device_manager_host import host
from tools.device_manager_spawn import _error_reply, _shot_timeout, _unpack_device

logger = logging.getLogger("tools.device_manager")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png_result(data: bytes) -> dict:
    """Validate PNG magic bytes and wrap into the success dict, else an error."""
    if not data or not data.startswith(_PNG_MAGIC):
        return _error_reply("capture did not return a valid PNG image")
    return {"content": data, "media_type": "image/png", "error": None}


def _png_from_file(rc: int, path: str) -> dict | None:
    """The PNG reply read back from *path* when the tool succeeded, else None."""
    if rc == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as handle:
            return _png_result(handle.read())
    return None


def _unlink_quiet(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


async def _screenshot_android(device_id: str) -> dict:
    h = host()
    cmd = [h._adb_binary(), "-s", device_id, "exec-out", "screencap", "-p"]
    rc, out, err = await h._run(cmd, _shot_timeout())
    if rc != 0:
        return _error_reply(
            err.decode(errors="replace")[:200] or "adb screencap failed"
        )
    return _png_result(out)


# Simulator screenshot via a secure temp file.
#
# Older Xcode supported streaming with the documented ``-`` (stdout) target,
# but newer versions treat ``-`` as a literal filename — writing a stray
# ``./-`` file and nothing to stdout — so the only portable target is a real
# file path (same pattern as the physical-device capture).
async def _screenshot_ios_simulator(device_id: str) -> dict:
    """Simulator screenshot via a secure temp file."""
    path = host().make_secure_temp_path(prefix="qa_sim_shot_", suffix=".png")
    try:
        cmd = ["xcrun", "simctl", "io", device_id, "screenshot", path]
        rc, _out, err = await host()._run(cmd, _shot_timeout())
        return _png_from_file(rc, path) or _error_reply(
            err.decode(errors="replace")[:200] or "simctl screenshot failed"
        )
    finally:
        _unlink_quiet(path)


async def _devicectl_shot(device_id: str, path: str) -> dict | None:
    """The devicectl screenshot, or None when it failed (caller falls back)."""
    cmd = ["xcrun", "devicectl", "device", "screenshot", "--device", device_id, path]
    try:
        rc, _out, _err = await host()._run(cmd, _shot_timeout())
        got = _png_from_file(rc, path)
    except FileNotFoundError:
        logger.info("device_manager: devicectl missing -- trying idevicescreenshot")
        return None
    if got is None:
        logger.info(
            "device_manager: devicectl screenshot failed -- trying idevicescreenshot"
        )
    return got


async def _idevice_shot(device_id: str, path: str) -> dict:
    cmd = ["idevicescreenshot", "-u", device_id, path]
    rc, _out, err = await host()._run(cmd, _shot_timeout())
    return _png_from_file(rc, path) or _error_reply(
        err.decode(errors="replace")[:200] or "physical iOS screenshot failed"
    )


# Physical iOS screenshot: devicectl to a temp file, idevicescreenshot fallback.
#
# devicectl and idevicescreenshot both write to a file rather than stdout, so
# the shot is written to a secure temp path, read back, and unlinked.
async def _screenshot_ios_physical(device_id: str) -> dict:
    """Physical iOS screenshot: devicectl to a temp file, idevicescreenshot fallback."""
    path = host().make_secure_temp_path(prefix="qa_device_shot_", suffix=".png")
    try:
        return await _devicectl_shot(device_id, path) or await _idevice_shot(
            device_id, path
        )
    finally:
        _unlink_quiet(path)


async def _capture(device_id: str, platform: str, kind: str) -> dict:
    if platform == "android":
        return await _screenshot_android(device_id)
    if platform == "ios" and kind == "simulator":
        return await _screenshot_ios_simulator(device_id)
    if platform == "ios":
        return await _screenshot_ios_physical(device_id)
    return _error_reply(f"unsupported device platform: {platform or 'unknown'}")


# Capture a PNG screenshot from *device*.
#
# Shape: ``{"content": <png bytes>, "media_type": "image/png", "error": None}``
# on success, else ``{"error": str, "content": None}``. Never raises. The device
# id is whitelist-validated before it is passed to any subprocess.
async def capture_screenshot(device: dict) -> dict:
    """Capture a PNG screenshot from *device* (never raises; ``error`` key)."""
    device_id, platform, kind = _unpack_device(device)
    if not _valid_device_id(device_id):
        return _error_reply("invalid or missing device id")
    try:
        return await _capture(device_id, platform, kind)
    except FileNotFoundError as exc:
        logger.info("device_manager: capture tool missing: %s", exc)
        return _error_reply(
            "the required capture tool is not installed on this machine"
        )
    except asyncio.TimeoutError:
        return _error_reply("the screenshot capture timed out")
    except Exception as exc:
        logger.exception("device_manager: unexpected screenshot error")
        return _error_reply(str(exc))
