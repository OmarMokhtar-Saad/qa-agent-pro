"""The ONE place the optional lanes are imported (LAZY S1).

A distribution without the mobile lane ships neither ``handlers/mobile`` nor
``tools/mobile`` (``scripts/build_dist.py``), and no dist ships the api lane
(``handlers/api_lane``). Every other handler reaches a lane only through the
names below, which are ``None`` when the lane is absent. Nothing else imports
``handlers.mobile`` / ``handlers.api_lane`` (``.importlinter`` pins that).

Only the absence of the lane's own package reads as "no lane": a real typo or
a missing third-party dependency inside a lane that IS shipped still raises.
"""

from __future__ import annotations

from typing import Any, cast

_MOBILE_PACKAGES = ("handlers.mobile", "tools.mobile")
_API_PACKAGES = ("handlers.api_lane",)

try:
    import handlers.mobile as mobile_lane
    import tools.mobile.adb as adb
    import tools.mobile.emulator as emulator
    import tools.mobile.locks as locks
    import tools.mobile.peek as peek
    import tools.mobile.render as render
    import tools.mobile.script_help as script_help
    import tools.mobile.sdk_locator as sdk_locator
    import tools.mobile.watch as watch
except ImportError as exc:  # pragma: no cover - the dist-simulation test covers it
    if (exc.name or "") not in _MOBILE_PACKAGES:
        raise
    mobile_lane = cast("Any", None)
    adb = cast("Any", None)
    emulator = cast("Any", None)
    locks = cast("Any", None)
    peek = cast("Any", None)
    render = cast("Any", None)
    script_help = cast("Any", None)
    sdk_locator = cast("Any", None)
    watch = cast("Any", None)

try:
    import handlers.api_lane as api_lane
except ImportError as exc:  # pragma: no cover - no dist ships the api lane
    if (exc.name or "") not in _API_PACKAGES:
        raise
    api_lane = cast("Any", None)
