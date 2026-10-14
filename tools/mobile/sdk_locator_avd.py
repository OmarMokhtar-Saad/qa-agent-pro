"""AVD ``config.ini`` readers and the Android Studio probe for :mod:`tools.mobile.sdk_locator`."""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings_guard import never_raise
from tools.mobile import platform_info
from tools.process_env import get_env_str, home_dir

logger = logging.getLogger("tools.mobile.sdk_locator")

#: Bytes of one AVD ``config.ini`` read to find its system image. A real one is
#: a few KB; the cap keeps a corrupt or hostile file from being read whole on
#: every machine-report poll. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
AVD_CONFIG_MAX_BYTES = 65536

#: ``config.ini`` keys of an AVD's panel, and the name each is reported under.
_AVD_DISPLAY_KEYS = {
    "hw.lcd.width": "width",
    "hw.lcd.height": "height",
    "hw.lcd.density": "density",
}


def _avd_name_is_safe(name: str) -> bool:
    """An AVD name is a single path component: no separator, no leading dot."""
    return (
        bool(name)
        and not name.startswith(".")
        and all(c.isalnum() or c in "._-" for c in name)
    )


def avd_name_is_safe(name: str) -> bool:
    """Public name of :func:`_avd_name_is_safe`, for ``avd_manage``: one rule, one producer."""
    return _avd_name_is_safe(name)


def _avd_homes() -> list[Path]:
    homes = []
    raw = get_env_str("ANDROID_AVD_HOME")
    if raw:
        homes.append(Path(raw).expanduser())
    for var in ("ANDROID_USER_HOME", "ANDROID_EMULATOR_HOME"):
        raw = get_env_str(var)
        if raw:
            homes.append(Path(raw).expanduser() / "avd")
    homes.append(home_dir() / ".android" / "avd")
    return homes


def _read_head(config: Path) -> str | None:
    try:
        if not config.is_file():
            return None
        with config.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(AVD_CONFIG_MAX_BYTES)
    except OSError:
        return None


# The head of every ``config.ini`` AVD *name* has, in the emulator's order:
# ``ANDROID_AVD_HOME``, ``ANDROID_USER_HOME/avd``, ``ANDROID_EMULATOR_HOME/avd``,
# ``~/.android/avd``. Each read is capped at ``AVD_CONFIG_MAX_BYTES``; an
# unreadable file is skipped. *name* must already have passed
# ``_avd_name_is_safe``.
def _avd_config_texts(name: str) -> list[str]:
    """The head of every ``config.ini`` AVD *name* has, in the emulator's order.

    The homes: ``ANDROID_AVD_HOME``, ``ANDROID_USER_HOME/avd``,
    ``ANDROID_EMULATOR_HOME/avd``, ``~/.android/avd``. Each read is capped at
    ``AVD_CONFIG_MAX_BYTES``; an unreadable file is skipped. *name* must
    already have passed ``_avd_name_is_safe``.
    """
    heads = (_read_head(h / (name + ".avd") / "config.ini") for h in _avd_homes())
    return [text for text in heads if text is not None]


def _display_fields(text: str) -> dict:
    found = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        field = _AVD_DISPLAY_KEYS.get(key.strip())
        value = value.strip()
        if field and value.isdigit() and len(value) <= 6:
            found[field] = int(value)
    return found


# ``{"error", "content": {"width", "height", "density"}}``: whichever of
# ``hw.lcd.width``/``hw.lcd.height``/``hw.lcd.density`` the first config
# carrying any of them states; ``{}`` when the name is not a plain AVD name or
# nothing is found. Feeds the heavy-AVD warning in qa-doctor. Never raises.
#
# ``{"error", "content": {"width", "height", "density"}}`` -- whichever of
# ``hw.lcd.width``/``hw.lcd.height``/``hw.lcd.density`` the first config
# carrying any of them states; ``{}`` when the name is not a plain AVD name
# or nothing is found. Feeds the heavy-AVD warning in qa-doctor (fix round
# 2, item 7). Never raises.
def avd_display(avd: object) -> dict:
    """An AVD's configured panel, read from its ``config.ini`` with no device."""
    try:
        name = str(avd or "").strip()
        if not _avd_name_is_safe(name):
            return {"error": None, "content": {}}
        for text in _avd_config_texts(name):
            found = _display_fields(text)
            if found:
                return {"error": None, "content": found}
        return {"error": None, "content": {}}
    except Exception as exc:
        logger.exception("mobile.sdk_locator.avd_display failed")
        return {"error": str(exc), "content": None}


def _image_from_text(text: str) -> str | None:
    """The package form of ``image.sysdir.1`` (``""`` if malformed); None if absent."""
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() != "image.sysdir.1":
            continue
        parts = [p for p in value.strip().replace("\\", "/").split("/") if p]
        if (
            len(parts) == 4
            and parts[0] == "system-images"
            and all(_avd_name_is_safe(part) for part in parts)
        ):
            return ";".join(parts)
        return ""
    return None


# ``{"error", "content": "system-images;android-35;google_apis;x86_64" or ""}``.
# Read from ``image.sysdir.1`` in the AVD's ``config.ini`` -- the exact tag
# Android Studio chose, with no device call. ``""`` when the name is not a plain
# AVD name, or the config or its key cannot be found. Never raises.
#
# ``{"error", "content": "system-images;android-35;google_apis;x86_64" or ""}``.
# Read from ``image.sysdir.1`` in the AVD's ``config.ini`` -- the exact tag
# Android Studio chose, with no device call, so it is pinned without a
# device. The AVD homes are searched in the emulator's own order:
# ``ANDROID_AVD_HOME``, ``ANDROID_USER_HOME/avd``,
# ``ANDROID_EMULATOR_HOME/avd``, ``~/.android/avd``. ``""`` when the name is
# not a plain AVD name, or the config or its key cannot be found. Never
# raises.
def avd_system_image(avd: object) -> dict:
    """The system image an AVD boots, in the SDK's own package form."""
    try:
        name = str(avd or "").strip()
        if not _avd_name_is_safe(name):
            return {"error": None, "content": ""}
        for text in _avd_config_texts(name):
            image = _image_from_text(text)
            if image is not None:
                return {"error": None, "content": image}
        return {"error": None, "content": ""}
    except Exception as exc:
        logger.exception("mobile.sdk_locator.avd_system_image failed")
        return {"error": str(exc), "content": None}


def _studio_on_windows() -> bool:
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = get_env_str(var)
        if base and (Path(base) / "Android" / "Android Studio").is_dir():
            return True
    return False


# The provisioner uses this for ONE decision the user settled: SDK licences are
# auto-accepted only when Android Studio is ABSENT. Where Studio exists, the
# tester has their own licence state and we do not answer a legal prompt.
def studio_present() -> bool:
    """True when Android Studio appears to be installed on this host.

    The provisioner uses this for ONE decision the user settled: SDK licences
    are auto-accepted only when Android Studio is ABSENT. Where Studio exists,
    the tester has their own licence state and we do not answer a legal prompt
    on their behalf.
    """
    with never_raise(
        logger,
        "mobile.sdk_locator: could not test for Android Studio",
        level=logging.INFO,
    ):
        if platform_info.is_macos():
            return Path("/Applications/Android Studio.app").is_dir()
        return platform_info.is_windows() and _studio_on_windows()
    return False
