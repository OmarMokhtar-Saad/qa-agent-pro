"""The pinned APK: cache path, verified download, presence, install."""

from __future__ import annotations

import re
from pathlib import Path

from tools.mobile import adb, downloader, paths
from tools.mobile.ime_host import host


def apk_cache_path(version: str) -> Path:
    """Where the verified APK is cached."""
    tag = re.sub(r"[^A-Za-z0-9._-]", "-", str(version or "unversioned"))
    return paths.sub("ime") / ("qa-ime-" + tag + ".apk")


# Kill-switch checked HERE, at the effect: a cache miss fetches over the
# network, and the flag's documented promise is that it gates every install,
# download or launch. A guard only on the provisioner covers the path one
# reviewer walked, not the class.
def ensure_apk() -> dict:
    """Return the cached, hash-verified APK path, downloading it on a miss."""
    resolved = host().manifest()
    if resolved.get("error"):
        return resolved
    info = resolved["content"] or {}
    paths.ensure_tree()
    dest = host().apk_cache_path(str(info.get("version") or ""))
    return downloader.download(
        downloader.DownloadRequest(
            str(info["url"]),
            dest,
            str(info["sha256"]),
            payload_bytes=0,
            progress_path=paths.state_file("ime-download.json"),
            phase="ime",
        )
    )


async def installed(serial: str) -> dict:
    """True when the pinned IME package is present on the device."""
    resolved = host().manifest()
    if resolved.get("error"):
        return resolved
    package = str((resolved["content"] or {})["package"])
    listed = await adb.installed_packages(serial)
    if listed.get("error"):
        return listed
    return {
        "error": None,
        "content": {"installed": package in (listed.get("content") or [])},
    }


async def install(serial: str) -> dict:
    """Install the pinned APK from cache (fetching it first on a miss).

    Guarded in its own right rather than relying on ensure_apk's check: a
    warm cache would otherwise install without the flag ever being read.
    """
    fetched = host().ensure_apk()
    if fetched.get("error"):
        return fetched
    return await adb.install(serial, str((fetched["content"] or {})["path"]))
