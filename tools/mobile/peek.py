"""A light look at the device screen: the element list as text, nothing kept.

``qa_capture_screens(peek=true)`` ends here. It answers what is on the screen
right now with ONE accessibility dump rendered by ``perception.to_prompt_block``,
the same producer the run packets use, so the text is already wrapped as
untrusted device text. It takes no screenshot, writes no tray entry or capture
id, taps nothing and asks no question, so it needs no ``apply``.

The screen is built exactly as ``executor._dump`` builds it (activity from
``executor.resolve_activity``, display size and density from ``adb``), so a
``screen_id`` shown here is the id a trace would carry. When the element list is
not enough to act on (``perception.dump_unusable``) the reply says so and points
at the screenshot path; it never attaches a picture itself.

No new cap: the size bounds are the ones ``to_prompt_block`` and
``wrap_untrusted`` (on the adb error text) already apply.
Never raises.
"""

from __future__ import annotations

import logging

from tools.mobile import adb, executor, perception
from tools.mobile.providers import composite
from tools.untrusted import wrap_untrusted

logger = logging.getLogger(__name__)

_FALLBACK = "Call `qa_capture_screens` without `peek` for a screenshot."


async def peek(serial: str) -> str:
    """The current screen of *serial* as markdown. Never raises."""
    try:
        raw = await executor.dump_raw(serial)
        if raw.get("error"):
            return (
                "\u26a0\ufe0f Could not read the screen. Check the device is unlocked "
                "and connected (`qa_list_devices`).\n\n"
                + wrap_untrusted("adb error", str(raw["error"]))
            )
        sized = await adb.display_size(serial)
        dpi = await adb.display_density(serial)
        activity = await executor.resolve_activity(executor.Context(serial=serial))
        screen = composite.observe(
            raw.get("content"),
            activity,
            display=sized.get("content"),
            density=dpi.get("content"),
        )
        if not isinstance(screen, dict) or screen.get("error"):
            return (
                "\u26a0\ufe0f Could not read the screen: the dump could not be parsed. "
                + _FALLBACK
            )
        block = perception.to_prompt_block(screen)
        reason = perception.dump_unusable(screen)
        lines = [
            "## Screen peek",
            "",
            "Read-only: nothing was tapped, saved or attached.",
            "",
            block or "The screen shows no readable elements.",
        ]
        if reason:
            lines += [
                "",
                f"The element list is not enough to act on ({reason}). " + _FALLBACK,
            ]
        return "\n".join(lines)
    except Exception:
        logger.debug("peek failed", exc_info=True)
        return "\u26a0\ufe0f Could not read the screen. " + _FALLBACK
