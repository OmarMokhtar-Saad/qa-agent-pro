"""Which input method ids the device lists, and the listed spelling of ours."""

from __future__ import annotations

import logging

from tools.mobile import adb
from tools.mobile.ime_component import same_component
from tools.mobile.ime_consts import _IME_ID_RE, IME_NOT_LISTED
from tools.mobile.ime_host import host

logger = logging.getLogger("tools.mobile.ime")


def _ids_of(out: str) -> list[str]:
    """The distinct input method ids in *out*, in first-seen order."""
    ids: list[str] = []
    for found in _IME_ID_RE.findall(out):
        if found not in ids:
            ids.append(found)
    return ids


async def _list_ime_ids(serial: str) -> list[str]:
    """Every input method id the device lists, spelled as the device spells it.

    ``ime list -a -s`` first, ``ime list -a`` when that fails or lists nothing.
    Empty when neither answered: the caller decides what an unread list means.
    """
    for argv in (["ime", "list", "-a", "-s"], ["ime", "list", "-a"]):
        result = await adb.shell(serial, argv)
        if result.get("error"):
            continue
        body = result.get("content") or {}
        if int(body.get("rc") or 0) != 0:
            continue
        ids = _ids_of(str(body.get("out") or ""))
        if ids:
            return ids
    return []


def _pick_listed(listed: list[str], pinned: str) -> dict:
    """The listed spelling of the pinned component, or a refusal by name."""
    for candidate in listed:
        if same_component(candidate, pinned):
            return {"error": None, "content": candidate}
    return {
        "error": IME_NOT_LISTED % (pinned, ", ".join(listed[:8])[:400]),
        "content": None,
    }


# The manifest pins ``pkg/pkg.Class`` and a device stores and lists
# ``pkg/.Class``; the air session's API 35 device refused the pinned
# spelling on ``ime enable`` and nothing was ever typed. So the id sent is
# the LISTED one that :func:`same_component` matches, never the pin itself.
#
# A list that names other keyboards but not ours is a refusal by name
# (:data:`IME_NOT_LISTED`). A list that could not be read at all proves
# nothing, so the pinned id is sent and the device's own answer decides.
async def resolve_installed_id(serial: str) -> dict:
    """The QA keyboard's id AS THE DEVICE LISTS IT, for ``enable``/``select``."""
    resolved = host().manifest()
    if resolved.get("error"):
        return resolved
    pinned = str((resolved["content"] or {})["ime_id"])
    listed = await _list_ime_ids(serial)
    if not listed:
        logger.warning(
            "mobile.ime: the device listed no input methods; sending the pin"
        )
        return {"error": None, "content": pinned}
    return _pick_listed(listed, pinned)
