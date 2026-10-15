"""Keyboard switches that leave a held dump valid.

Every typing script ends by putting the tester's keyboard back (``ime set``,
``ime enable``, ``ime reset`` or ``settings put secure default_input_method``).
To :mod:`tools.mobile.dump_reuse` that is an unknown command, so the dump held
from the script's last read was thrown away and the next read paid 2.3-3.3 s
for the same screen.

A keyboard switch only changes what is on screen when a keyboard window is on
screen. So the switch is neutral exactly when EVERY dump held for the device
shows no keyboard window and no dump is in flight (an in-flight dump could
straddle the switch). Anything else, and any other command, still bumps the
generation. The check is by package name, so it is app-independent, and it
fails closed: a held dump that is not a dump, or none held, bumps.
"""

from __future__ import annotations

import re

from tools.mobile import dump_latency

_SWITCH = re.compile(
    r"(?:ime (?:set|enable|reset)(?: \S+)?"
    r"|settings put secure default_input_method \S+)"
)

_KEYBOARD_NODE = re.compile(
    r'<node\b[^>]*?\bpackage="[^"]*'
    r"(?:inputmethod|keyboard|honeyboard|swiftkey|\.ime[.\"])",
    re.IGNORECASE,
)


def is_switch(body: str) -> bool:
    """True for a shell body that only switches, enables or resets a keyboard."""
    return _SWITCH.fullmatch(body.strip()) is not None


def shows_keyboard(content: object) -> bool:
    """True when a raw dump may show a keyboard window; fails closed on a non-dump."""
    if not isinstance(content, str) or "<hierarchy" not in content:
        return True
    return _KEYBOARD_NODE.search(content) is not None


def keeps_held(serial: str, body: str, held: list[object]) -> bool:
    """Whether a keyboard switch *body* leaves the dumps *held* for *serial* valid."""
    if not held or not is_switch(body):
        return False
    if dump_latency.inflight(serial) is not None:
        return False
    return not any(shows_keyboard(content) for content in held)
