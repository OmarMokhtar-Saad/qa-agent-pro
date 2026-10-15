"""Device facts and the focused activity: ``device_facts``, ``current_activity``."""

from __future__ import annotations

import re

from tools.mobile.adb_host import host


# WHY THE SERIAL PREFIX IS NOT ENOUGH, even though it is free. ``emulator-*``
# is how the local emulator presents itself over USB-style transport, but an
# emulator reached over TCP (``adb connect``, a remote or containerised one)
# arrives as ``host:port`` and would be reported as the tester's PHONE. The
# kind is the whole point of this record -- a pass on an emulator is weaker
# evidence than a pass on real hardware, and a mislabel makes the evidence
# worse than absent -- so it is CONFIRMED against ``ro.kernel.qemu``, which
# the device answers about itself.
async def _kind(text: str) -> str:
    qemu = await host().getprop(text, "ro.kernel.qemu")
    is_qemu = str((qemu or {}).get("content") or "").strip() == "1"
    return "emulator" if (text.startswith("emulator-") or is_qemu) else "physical"


async def _fill(facts: dict, text: str, spec: tuple[str, str, int]) -> None:
    key, prop, width = spec
    result = await host().getprop(text, prop)
    if not (result or {}).get("error"):
        facts[key] = str(result.get("content") or "")[:width]


async def device_facts(serial: str) -> dict:
    """``{kind, model, api}`` for one device. ``kind`` is ``emulator`` or ``physical``.

    Three ``getprop`` reads, run ONCE per run at planning time and stored on the
    manifest, so nothing in the packet loop pays for them. Nothing here raises
    and nothing here is fatal: a property that cannot be read leaves its field
    empty, and an empty model is a worse report line, not a failed run.
    """
    text = str(serial or "").strip()
    facts = {"kind": "", "model": "", "api": ""}
    facts["kind"] = await _kind(text)
    await _fill(facts, text, ("model", "ro.product.model", 60))
    await _fill(facts, text, ("api", "ro.build.version.sdk", 8))
    return {"error": None, "content": facts}


def _focused_component(text: str) -> str:
    for line in text.splitlines():
        if "mCurrentFocus" not in line:
            continue
        found = re.search(r"([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)", line)
        if found:
            return found.group(1)[:200]
    return ""


focused_component = _focused_component


# The uiautomator dump does NOT carry the activity, so `perception` took it as
# a parameter and every caller passed the empty default -- the value was
# threaded end to end with no producer anywhere. `screen_id` therefore hashed
# the package and the top three texts only, and two screens of one app with
# similar wording could share an identity, which the report dedupes on and
# `assert screen_changed` compares.
#
# ``mCurrentFocus`` is read rather than ``dumpsys activity activities``: it is
# one short line, it names the window that actually has focus (which is what a
# tap will hit), and it does not depend on the activity-stack format, which
# differs across Android versions.


async def current_activity(serial: str, timeout: int = 20) -> dict:
    """The focused activity as ``package/Activity``, or "" when unknowable.

    An empty answer is a NORMAL result, not an error: a dialog or a system
    window can leave no resolvable component, and a screen identity that is
    weaker than it could be is far better than a refused dump.
    """
    result = await host().shell(serial, ["dumpsys", "window"], timeout=timeout)
    if result.get("error"):
        return {"error": None, "content": ""}
    text = str((result["content"] or {}).get("out") or "")
    return {"error": None, "content": _focused_component(text)}
