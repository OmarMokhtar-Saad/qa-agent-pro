"""B05: an installer, launcher or App Tester package is never the app under test."""

from __future__ import annotations

from handlers import _common
from handlers.mobile.run_request import _AppCall
from tools.mobile import target_pin
from tools.mobile.install_sources.app_tester_ui_labels import newest_app


async def swap_transient(call: _AppCall) -> str:
    """The typed package, unless it is an installer, launcher or App Tester id
    (B05): then the newest remembered app, or ``""`` so the app is asked."""
    typed = call.typed_package
    if not target_pin.transient(typed):
        return typed
    found = newest_app(call.serial)
    swapped = found.bundle if found else ""
    note = "`" + typed + "` is a system surface, not the app under test"
    note += ": testing `" + swapped + "` instead." if swapped else "."
    await _common.emit(call.progress, note)
    return swapped
