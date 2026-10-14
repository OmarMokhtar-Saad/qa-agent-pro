"""Replay engine: the typing function and the QA-keyboard-active check."""

from __future__ import annotations

from tools.mobile import ime, step_timing
from tools.mobile.replay.device_ops_consts import QA_IME_NOT_ACTIVE, QA_IME_SEEN_CAP


def _typer(fallback: str):
    """The typing function for this replay: the fallback one when the keyboard is down."""
    return ime.type_via_input if fallback else ime.type_text


async def _qa_ime_blocker(serial: str) -> dict | None:
    """A named blocker when the QA keyboard is not the active one, else None.

    ``keyboard_up`` selected it before the script started, but a tap can bring
    up another keyboard and every broadcast after that goes nowhere. A read
    error or a DIFFERENT keyboard stops the step; a blank read proves nothing
    and is left to the read-back verdict.
    """
    ours = str(((ime.manifest() or {}).get("content") or {}).get("ime_id") or "")
    current = await step_timing.timed("ime_check", ime.current_ime(serial))
    if current.get("error"):
        seen = "an unreadable setting: " + str(current["error"])[:120]
    else:
        seen = str(current.get("content") or "")
        if not seen or ime.same_component(seen, ours):
            return None
    # The package only, capped: the device-error detail is cut at 400
    # chars, and the note at the end is the part that must survive.
    package = seen.split("/", 1)[0][:QA_IME_SEEN_CAP]
    return {"error": QA_IME_NOT_ACTIVE % package, "content": None}
