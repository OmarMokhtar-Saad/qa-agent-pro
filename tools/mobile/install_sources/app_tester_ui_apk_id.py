"""App Tester UI: the Firebase app id of an INSTALLED app, read from its APK.

A Firebase app carries its id as the ``google_app_id`` string resource (in
``resources.arsc``). Reading it once makes the App Tester page reachable by link
with no list scan, for any app. Best effort: any miss returns ``""``.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path

from tools.mobile import adb
from tools.mobile.install_sources import app_tester_ui_memory as memory
from tools.mobile.install_sources.arsc_string import string_resource

_ID = r"\d+:\d+:android:[0-9a-fA-F]+"
_UTF8 = re.compile(_ID.encode())
_UTF16 = re.compile(
    rb"(?:[0-9]\x00)+:\x00(?:[0-9]\x00)+:\x00a\x00n\x00d\x00r\x00o\x00i\x00d\x00:\x00"
    rb"(?:[0-9a-fA-F]\x00)+"
)


def app_id_in_apk(path: str) -> str:
    """The Firebase app id of the APK, else ``""``.

    The ``google_app_id`` string resource wins; a table that cannot be read that
    way falls back to the first id-shaped string in it.
    """
    try:
        with zipfile.ZipFile(path) as apk:
            blob = apk.read("resources.arsc")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""
    named = string_resource(blob, "google_app_id")
    if _UTF8.fullmatch(named.encode()):
        return named
    hit = _UTF8.search(blob)
    if hit:
        return hit.group(0).decode()
    hit16 = _UTF16.search(blob)
    return hit16.group(0).decode("utf-16-le") if hit16 else ""


async def _base_apk(serial: str, package: str) -> str:
    listed = await adb.shell(serial, ["pm", "path", package])
    for line in str((listed.get("content") or "")).splitlines():
        if line.startswith("package:") and line.endswith("base.apk"):
            return line[len("package:") :].strip()
    return ""


async def learn_app_id(serial: str, package: str) -> str:
    """Remember and return the id of an installed *package* (``""`` if none)."""
    if not package or memory.known_app_id(package):
        return memory.known_app_id(package)
    if memory.apk_has_no_id(serial, package):
        return ""
    remote = await _base_apk(serial, package)
    if not remote:
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        local = str(Path(tmp) / "base.apk")
        if (await adb.pull(serial, remote, local)).get("error"):
            return ""
        app_id = app_id_in_apk(local)
    memory.remember_app_id(package, app_id)
    if not app_id:
        memory.note_apk_without_id(serial, package)
    return app_id
