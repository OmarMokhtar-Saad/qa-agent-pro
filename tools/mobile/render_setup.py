"""What the tester must do by hand before this lane can boot anything.

Tenth layer of the ``render_*`` split of ``render.py``.

``setup_guide`` -- this server downloads no Android SDK and creates no emulator
unasked (docs/RETIRED_CAPABILITIES.md -> 6), so a machine without them gets
these steps instead. Any ``reason`` other than ``no_avd`` reads as ``no_sdk``:
an unknown reason must still lead with the install step.

``{"setup_required": True, "reason", "steps": [str, ...],
"links": {"android_studio", "create_avd"}}``.

``setup_guide_block`` -- the setup guide as a reply: readable steps, then the
dict itself. The steps are for the tester; the fenced JSON copy is for the chat
model, so it sees ``setup_required: true`` without parsing prose. Nothing is
downloaded or started on this path, and the reply says so.
"""

from __future__ import annotations

import json

#: The official Android Studio download page. ONE constant: the setup guide,
#: the qa-doctor row and docs/MOBILE_TESTING.md name the same URL.
ANDROID_STUDIO_URL = "https://developer.android.com/studio"

#: The official "create and manage virtual devices" page.
AVD_GUIDE_URL = "https://developer.android.com/studio/run/managing-avds"

#: Why a setup guide was returned. ``no_sdk``: no emulator binary was located,
#: so Android Studio comes first. ``no_avd``: the SDK is there but no virtual
#: device exists yet.
SETUP_NO_SDK = "no_sdk"
SETUP_NO_AVD = "no_avd"

_INSTALL_STEP = (
    "Install Android Studio from "
    + ANDROID_STUDIO_URL
    + " and open it once, so it installs the Android SDK and the emulator."
)
_COMMON_STEPS = (
    "In Android Studio open Device Manager and create a virtual device "
    "(AVD). Pick a system image marked Google Play, so apps can be "
    "installed from the Play Store. Pick a Google APIs image instead only "
    "if you want HTTPS traffic decrypted: a Play image cannot be rooted. "
    "Guide: " + AVD_GUIDE_URL,
    "Run `qa-doctor` and check that the mobile device lane now finds the "
    "SDK and your AVD.",
    "Call `qa_mobile_test` again.",
)
_GUIDE_LEAD = (
    "\n\nThis server does not download an Android SDK or create an "
    "emulator. Nothing was downloaded or started. Do this once:\n\n"
)


def setup_guide(reason: str) -> dict:
    """What the tester must do by hand before this lane can boot anything."""
    missing_avd_only = reason == SETUP_NO_AVD
    steps = [] if missing_avd_only else [_INSTALL_STEP]
    steps += _COMMON_STEPS
    return {
        "setup_required": True,
        "reason": SETUP_NO_AVD if missing_avd_only else SETUP_NO_SDK,
        "steps": steps,
        "links": {"android_studio": ANDROID_STUDIO_URL, "create_avd": AVD_GUIDE_URL},
    }


def setup_guide_block(guide: object) -> str:
    """The setup guide as a reply: readable steps, then the dict itself."""
    body = guide if isinstance(guide, dict) else setup_guide(SETUP_NO_SDK)
    title = (
        "## No Android emulator (AVD) on this machine yet"
        if body.get("reason") == SETUP_NO_AVD
        else "## Android Studio is needed first"
    )
    listed = body.get("steps") or []
    steps = "\n".join(str(i + 1) + ". " + str(step) for i, step in enumerate(listed))
    json_text = json.dumps(body, indent=2)
    return title + _GUIDE_LEAD + steps + "\n\n```json\n" + json_text + "\n```"
