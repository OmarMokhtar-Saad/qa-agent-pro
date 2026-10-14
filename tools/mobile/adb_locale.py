"""Device language: ``runtime_locale``, ``persisted_locale``, ``set_locale`` (moved from adb.py)."""

from __future__ import annotations

from tools.mobile.adb_consts_dev import LOCALE_TAG, PERSIST_LOCALE_PROP
from tools.mobile.adb_host import host
from tools.mobile.adb_reply import rc_failure


# ``config: mcc310-mnc260-en-rUS-ldltr-sw411dp-...``. Split on ``-`` and take
# the first token that is exactly two lowercase letters, plus the ``rXX``
# token after it when there is one. A regex over the whole line would have to
# exclude ``ldltr`` and every qualifier Android adds later by hand; the token
# walk cannot match one, because no qualifier is two letters long.
def _tag_from_config(line: str) -> str:
    """The BCP-47 tag inside one ``am get-config`` line, or ``""``."""
    # DROP THE LABEL FIRST. `am get-config` answers `config: ar-rEG-ldltr-...`,
    # and splitting the raw line on `-` yields `config: ar` as one token -- two
    # words, not two letters -- so a locale in FIRST position was never found.
    # It only appeared to work where mcc/mnc came first and pushed the tag off
    # the label.
    text = str(line or "")
    _, _, after = text.partition(":")
    parts = [part for part in (after or text).strip().split("-") if part]
    for index, part in enumerate(parts):
        if len(part) != 2 or not part.isalpha() or not part.islower():
            continue
        following = parts[index + 1] if index + 1 < len(parts) else ""
        if len(following) == 3 and following[0] == "r" and following[1:].isupper():
            return part + "-" + following[1:]
        return part
    return ""


# ``am get-config`` reports the configuration apps actually resolve resources
# against, which is the only fact a report may echo: a run that says it ran in
# Arabic must mean the screens were Arabic, not that a property was written.
#
# A SECOND, WEAKER DERIVATION IS DELIBERATELY ABSENT. ``getprop
# persist.sys.locale`` answers a DIFFERENT question -- what survives a reboot
# -- and :func:`persisted_locale` owns it under its own name. Two names
# because the two legitimately disagree: writing the property on a running
# device changes the second and not the first until the device restarts, and
# that gap IS the failure this lane has to be able to see.
#
# An unparseable answer is ``""`` with NO error: an unknown locale is a blank
# in the report, never a guess and never a refused run.
async def runtime_locale(serial: str) -> dict:
    """THE locale the device is RENDERING IN. ``{"error", "content": tag}``."""
    result = await host().shell(serial, ["am", "get-config"], timeout=30)
    if result.get("error"):
        return result
    body = result.get("content") or {}
    rc = int(body.get("rc") or 0)
    if rc != 0:
        return rc_failure("adb shell am get-config", body, rc)
    for line in str(body.get("out") or "").splitlines():
        if line.strip().startswith("config:"):
            return {"error": None, "content": host()._tag_from_config(line)}
    return {"error": None, "content": ""}


async def persisted_locale(serial: str) -> dict:
    """The locale that will survive a reboot: ``persist.sys.locale``, or ``""``.

    A second name for a second question -- see :func:`runtime_locale` for why
    these are not one function with a caveat.
    """
    return await host().getprop(serial, PERSIST_LOCALE_PROP)


def _refuse_tag(wanted: str) -> dict:
    return {
        "error": (
            "Refusing to set the device language to "
            + repr(wanted[:40])
            + ". Give a language tag like `ar`, `ar-EG` or `en-US`."
        ),
        "content": None,
    }


def _readback(seen: dict, persisted: dict) -> dict:
    return {
        "error": None,
        "content": {
            "runtime": str(seen.get("content") or ""),
            "persisted": (
                "" if persisted.get("error") else str(persisted.get("content") or "")
            ),
        },
    }


# ``{"error", "content": {"runtime", "persisted"}}``.
#
# THE READ-BACK IS THE RESULT, and the exit code is not read as evidence at
# all. ``setprop persist.sys.locale`` exits 0 on builds where it changed
# nothing -- ``persist.sys.*`` needs root, which the ``google_apis_playstore``
# user build this lane provisions does not give adb -- and even where the
# write lands, the running system keeps its old configuration until it
# restarts. So this reports what :func:`runtime_locale` says AFTERWARDS and
# lets the caller compare.
#
# Guarded by the kill-switch at the innermost function that changes the
# device, exactly as ``install``/``uninstall`` are and for the reason
# ``emulator.start`` states: a guard on a caller is only as good as the list
# of callers.
async def set_locale(serial: str, tag: str) -> dict:
    """Ask the device for *tag*, then CONFIRM what it is rendering in."""
    wanted = str(tag or "").strip()
    if not LOCALE_TAG.match(wanted):
        return _refuse_tag(wanted)
    await host().shell(serial, ["setprop", PERSIST_LOCALE_PROP, wanted], timeout=30)
    seen = await host().runtime_locale(serial)
    if seen.get("error"):
        return seen
    persisted = await host().persisted_locale(serial)
    return _readback(seen, persisted)
