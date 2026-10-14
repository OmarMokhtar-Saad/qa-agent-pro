"""Argument object and reply texts shared by the avd_manage leaves.

The rules, in the order they are checked:

* an AVD name must pass ``sdk_locator.avd_name_is_safe``, else the call is
  REFUSED BY NAME and nothing runs;
* ``delete`` needs ``confirm_destructive`` to be exactly ``True`` (the tester's
  yes) AND ``apply=true``; it removes ONE AVD that exists and is not running --
  the only argv it ever builds is ``delete avd -n NAME``;
* ``boot`` and ``create`` need ``apply=true`` and run ``emulator -accel-check``
  FIRST. A host without a working hypervisor is only EXPLAINED (the fix text
  from ``platform_info`` and "use a real device"): no driver is installed and
  nothing is started or created;
* ``create`` uses a system image that is ALREADY installed under
  ``<sdk>/system-images``. It never downloads one (``sdkmanager`` is never
  run) and never passes ``--force``, so an existing AVD is never overwritten;
* ``boot`` is ``emulator.start`` -- the one spawn site -- after ``find_running``,
  so an AVD that is already running is reused, not started twice.

``list`` is read-only. Text a tool printed goes through ``wrap_untrusted``; AVD
names are device-side text and go through ``single_line``. Every subprocess is
``platform_info._run_sync`` on a worker thread with a timeout. Never raises.
"""

from __future__ import annotations

from dataclasses import dataclass

_WARN = "⚠️ "
_REAL_DEVICE = (
    "A real Android device needs no emulator: connect one with USB debugging "
    "on, then call `qa_list_devices`."
)
_GET_AN_IMAGE = (
    "No system image is installed, and this server downloads none. Install one "
    "yourself: Android Studio > Tools > SDK Manager > SDK Platforms, tick "
    "'Show Package Details' and pick a 'Google APIs' or 'Google Play' system "
    "image that matches this computer's CPU (arm64 on Apple silicon, x86_64 "
    "otherwise). Then call this again."
)
_ODD_IMAGES = (
    "%d system-image folder(s) skipped: a folder name with a character other "
    "than letters, digits, `.`, `_`, `-` cannot be used here. Android Studio "
    "can still use it."
)
_NO_AVDMANAGER = (
    "`avdmanager` was not found in the Android SDK (it ships with the 'Android "
    "SDK Command-line Tools'). Install them in Android Studio > SDK Manager > "
    "SDK Tools, then call this again."
)


@dataclass(frozen=True)
class AvdOpts:
    """The keyword arguments of one ``manage`` call. *apply* and
    *confirm_destructive* are judged by identity with ``True``, never by truth."""

    avd: str = ""
    system_image: str = ""
    apply: bool = False
    confirm_destructive: bool = False
