"""Install the pinned QA keyboard; replace OUR OWN package once on a key mismatch."""

from __future__ import annotations

from tools.mobile import adb, ime
from tools.mobile.ime_host import host
from tools.mobile.install_errors import UPDATE_INCOMPATIBLE


def _ours() -> str:
    """The pinned keyboard's package name, or ``""`` when the manifest names none."""
    return str((host().manifest().get("content") or {}).get("package") or "")


# The keyboard is ours and holds no tester data, so a signature mismatch is
# fixed by removing it and installing the pinned build once. The caller has
# already remembered the tester's keyboard (ime.json "previous"), so the restore
# path still gives that one back; nothing else is ever uninstalled.
async def install_pinned(serial: str) -> dict:
    """Install the pinned APK; on a key mismatch replace OUR package, once."""
    done = await ime.install(serial)
    package = _ours()
    if done.get("code") != UPDATE_INCOMPATIBLE or not package:
        return done
    if (await adb.uninstall(serial, package)).get("error"):
        return done
    return await ime.install(serial)
