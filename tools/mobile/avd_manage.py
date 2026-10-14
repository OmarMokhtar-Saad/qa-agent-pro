"""List, boot, create and delete Android virtual devices (AVDs) for the mobile lane.

``qa_mobile_test(emulator=list|boot|create|delete)`` ends here. A model that was
told "there is no AVD" used to run ``avdmanager`` itself through a raw shell and
skip the destructive guard; this module is the one governed path for that work.

The rules, in the order they are checked, are in ``avd_manage_args``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from tools.mobile import avd_manage_host, emulator, sdk_locator
from tools.mobile.avd_manage_actions import _boot, _create, _delete
from tools.mobile.avd_manage_args import (  # noqa: F401
    _GET_AN_IMAGE,
    _NO_AVDMANAGER,
    _ODD_IMAGES,
    _REAL_DEVICE,
    _WARN,
    AvdOpts,
)
from tools.mobile.avd_manage_images import (  # noqa: F401
    _accel_gate,
    _accel_warning,
    _avd_names,
    _avdmanager,
    _avds_block,
    _images_block,
    _list,
    _live_serials,
    _names_line,
    _tools,
)

logger = logging.getLogger(__name__)

#: Seconds one ``avdmanager`` call may run. It is a JVM start plus a few small
#: file writes; the long case is a cold disk. A tester waits inside an MCP
#: reply, so a raise here only lengthens a hang. Bounded from above in
#: ``tests/mobile/test_mobile_bounds_upper.py``.
AVDMANAGER_TIMEOUT_S = 120

#: Installed system images shown in one reply. Display only: the membership
#: check for ``create`` reads the FULL list.
MAX_IMAGES_LISTED = 40

#: AVDs shown in one reply. Display only.
MAX_AVDS_LISTED = 40

ACTIONS = ("list", "boot", "create", "delete")


def _part_ok(part: str) -> bool:
    """One folder name of an image id: ASCII letters, digits, ``.``, ``_``, ``-``."""
    return bool(part) and all(c.isascii() and (c.isalnum() or c in "._-") for c in part)


def _entries(folder: Path) -> list[Path]:
    """What ``folder`` holds, sorted; ``[]`` when it cannot be listed. A plain file
    among the image folders (a ``.DS_Store``) must not end the whole scan."""
    try:
        return sorted(folder.iterdir())
    except OSError:
        return []


def _image_parts(base: Path) -> list[tuple[Path, Path, Path]]:
    """Every ``api/tag/abi`` folder triple under *base* that holds an image."""
    return [
        (api, tag, abi)
        for api in _entries(base)
        for tag in _entries(api)
        for abi in _entries(tag)
        if (abi / "source.properties").is_file() or (abi / "package.xml").is_file()
    ]


# ``(image ids, image folders skipped for an odd name)``.
#
# An image is ``<sdk>/system-images/<api>/<tag>/<abi>/`` holding a
# ``source.properties`` or ``package.xml``. Reads directory names only.
# A folder whose api, tag or abi name fails ``_part_ok`` is counted, not
# returned: the id is shown in a reply and goes into the avdmanager argv,
# which on Windows passes through ``cmd``.
def _scan_images(sdk_root: str) -> tuple[list[str], int]:
    if not sdk_root:
        return [], 0
    try:
        found = _image_parts(Path(sdk_root) / "system-images")
    except OSError:
        return [], 0
    good = [t for t in found if all(_part_ok(p.name) for p in t)]
    return [";".join(("system-images", *(p.name for p in t))) for t in good], len(
        found
    ) - len(good)


def installed_images(sdk_root: str) -> list[str]:
    """Package ids of the system images already on disk, e.g.
    ``system-images;android-34;google_apis;x86_64``. ``[]`` when there are none.
    An oddly named folder is never one of them (see ``_scan_images``).
    """
    return _scan_images(sdk_root)[0]


def _started_refusal(name: str) -> str:
    """The refusal for an AVD this server started moments ago, or ``""``."""
    # adb lists an emulator only some seconds after it is spawned, so one this
    # server just started is not in `live` yet.
    if any(str(row.get("avd") or "") == name for row in emulator.recently_started()):
        return (
            _WARN
            + "That AVD was started moments ago and may still be booting, so it was "
            "not deleted. Close it first, or try again once it has stopped."
        )
    return ""


def _name_refusal(name: str) -> str:
    """The refusal for an unsafe AVD name, or ``""``."""
    # A leading dash would reach avdmanager as a flag (`-n --force`). ASCII only,
    # like the image ids; Android Studio is believed to build no other AVD id.
    if (
        name.startswith("-")
        or not name.isascii()
        or not sdk_locator.avd_name_is_safe(name)
    ):
        return (
            _WARN
            + "That is not a safe AVD name (ASCII letters, digits, `.`, `_` and `-`; "
            "no leading dot or dash). Nothing was run."
        )
    return ""


def _flag_refusal(what: str, opts: AvdOpts) -> str:
    """The refusal for a change action missing its confirmation, or ``""``."""
    if what == "delete" and opts.confirm_destructive is not True:
        return (
            _WARN + "Deleting an AVD is permanent. Ask the tester; only after they say "
            "yes call again with `confirm_destructive=true`. Nothing was deleted."
        )
    if opts.apply is not True:
        return (
            _WARN + "`emulator=%s` changes this computer, so it needs `apply=true`. "
            "Nothing was run." % what
        )
    return ""


def _refusal(what: str, name: str, opts: AvdOpts) -> str:
    """The refusal markdown for a change action, or ``""`` when it may run."""
    if not name:
        return _WARN + "`emulator=%s` needs `avd=<name>`." % what
    return _name_refusal(name) or _flag_refusal(what, opts)


async def _dispatch(what: str, name: str, opts: AvdOpts) -> str:
    if what == "delete":
        return await _delete(name)
    if what == "boot":
        return await _boot(name)
    return await _create(name, str(opts.system_image or "").strip())


_UNKNOWN = "Unknown emulator action. Use one of: list, boot, create, delete."
_FAILED = (
    "The emulator action failed unexpectedly; nothing further was run. See the "
    "server log."
)


# Run one AVD action and return markdown. Never raises.
#
# Refusals come before anything runs, in this order: unknown action, missing
# or unsafe name, ``delete`` without ``confirm_destructive is True``, any
# change without ``apply is True``. *confirm_destructive* and *apply* must be
# the literal ``True``: a truthy string does not count.
async def manage(action: str, opts: AvdOpts = AvdOpts()) -> str:
    try:
        what = str(action or "").strip().lower()
        if what == "list":
            return await _list()
        if what not in ACTIONS:
            return _WARN + _UNKNOWN
        name = str(opts.avd or "").strip()
        refusal = _refusal(what, name, opts)
        return refusal or await _dispatch(what, name, opts)
    except Exception:
        logger.exception("mobile.avd_manage failed")
        return _WARN + _FAILED


avd_manage_host.bind(sys.modules[__name__])
