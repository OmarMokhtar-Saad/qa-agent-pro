"""Replay engine: bring the QA keyboard up before a script and put the old one back."""

from __future__ import annotations

import logging

from tools.mobile import (
    ime,
    ime_cache,
    ime_fresh,
    step_timing,
)
from tools.mobile.replay.device_ops_consts import KEYBOARD_NOTE, KEYBOARD_OPS

logger = logging.getLogger("tools.mobile.executor")


def needs_keyboard(script: object) -> bool:
    """True when *script* carries a ``type`` or ``clear`` step."""
    return any(
        str(getattr(item, "op", "") or "") in KEYBOARD_OPS
        for item in list(getattr(script, "actions", None) or [])
    )


# Remember the current keyboard; install, enable, select ours; QUERY once.
#
# The install is skipped when ``ime.installed`` says it is there, and the
# select when the current keyboard already IS ours. The one ``ime.probe`` at
# the end is the run's receiver check: every type/clear after it passes
# ``receiver_known=True`` and sends no QUERY of its own. ``content`` is
# ``{"previous": <id or "">}``; ``error`` names the step that failed, and a
# failure AFTER the select still carries ``previous`` so it is restored.
# Never raises.
async def keyboard_up(serial: str) -> dict:
    """Remember the current keyboard; install, enable, select ours; QUERY once."""
    try:
        ime_cache.invalidate(serial)
        remembered = await ime.remember_previous(serial)
        if remembered.get("error"):
            return _keyboard_failure(
                "reading the current keyboard failed: " + str(remembered["error"])
            )
        previous = str((remembered.get("content") or {}).get("previous") or "")
        failed = await _keyboard_install(serial)
        if failed is None:
            failed = await _keyboard_select(serial, previous)
        if failed is None:
            failed = await _keyboard_arm_and_probe(serial, previous)
        return failed or {"error": None, "content": {"previous": previous}}
    except Exception as exc:
        logger.exception("mobile.executor.keyboard_up failed")
        return {"error": "QA keyboard not set up: " + str(exc), "content": None}


def _keyboard_failure(reason: str, previous: str | None = None) -> dict:
    """The ``keyboard_up`` error shape; ``previous`` rides along once known."""
    content = None if previous is None else {"previous": previous}
    return {"error": "QA keyboard not set up: " + reason, "content": content}


async def _keyboard_install(serial: str) -> dict | None:
    """Install (when missing or a foreign build) and enable ours; a failure or None."""
    present = await ime.installed(serial)
    if present.get("error"):
        return _keyboard_failure(
            "checking whether it is installed failed: " + str(present["error"])
        )
    missing = not (present.get("content") or {}).get("installed")
    if missing or await ime.is_foreign_build(serial):
        put = await ime_fresh.install_pinned(serial)
        if put.get("error"):
            return _keyboard_failure("installing it failed: " + str(put["error"]))
    enabled = await ime.enable(serial)
    if enabled.get("error"):
        return _keyboard_failure("enabling it failed: " + str(enabled["error"]))
    return None


async def _keyboard_select(serial: str, previous: str) -> dict | None:
    """Select ours unless it is already the current keyboard; a failure or None."""
    ours = str(((ime.manifest() or {}).get("content") or {}).get("ime_id") or "")
    current = await step_timing.timed(
        "ime_check", ime_cache.current(serial, ime.current_ime)
    )
    if current.get("error") or not ime.same_component(current.get("content"), ours):
        chosen = await ime.select(serial)
        if chosen.get("error"):
            return _keyboard_failure(
                "selecting it failed: " + str(chosen["error"]), previous
            )
    return None


async def _keyboard_arm_and_probe(serial: str, previous: str) -> dict | None:
    """G4: arm before the receiver check. A refused or unexpected ARM stops the
    keyboard here, which `replay` turns into the usual typing fallback and notice.
    """
    armed = await ime.arm(serial)
    if armed.get("error"):
        return _keyboard_failure(str(armed["error"]), previous)
    probed = await ime.probe(serial)
    if probed.get("error") or not (probed.get("content") or {}).get("ok"):
        return _keyboard_failure(
            "it does not answer its status query: "
            + str(probed.get("error") or "no result= in the reply"),
            previous,
        )
    return None


async def keyboard_down(serial: str, previous: str) -> dict:
    """Put the remembered keyboard back. ``content.note`` is what to tell."""
    try:
        restored = await ime.restore_previous(serial, previous)
        if restored.get("error"):
            return {
                "error": "QA keyboard installed and selected; previous keyboard "
                "NOT restored: " + str(restored["error"]),
                "content": None,
            }
        return {"error": None, "content": {"note": KEYBOARD_NOTE}}
    except Exception as exc:
        logger.exception("mobile.executor.keyboard_down failed")
        return {
            "error": "QA keyboard installed and selected; previous keyboard "
            "NOT restored: " + str(exc),
            "content": None,
        }
