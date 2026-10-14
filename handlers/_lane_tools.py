"""Leaf: the ``tools.mobile`` modules for handlers that sit inside the lane's
import cycle (``_reply`` -> ``reply_devices`` / ``reply_device_probe``).

``handlers._lane`` cannot serve them: it imports ``handlers.mobile``, which
imports ``_reply`` back. This module imports nothing from ``handlers`` (like
``_lane_present``), so the names are ``None`` exactly when ``tools.mobile``
did not ship; any other missing module still raises.
"""

from __future__ import annotations

from typing import Any, cast

try:
    import tools.mobile.adb as adb
    import tools.mobile.emulator as emulator
    import tools.mobile.render as render
except ModuleNotFoundError as exc:  # pragma: no cover - dist-simulation covers it
    if (exc.name or "") != "tools.mobile" and not (exc.name or "").startswith(
        "tools.mobile."
    ):
        raise
    adb = cast("Any", None)
    emulator = cast("Any", None)
    render = cast("Any", None)
