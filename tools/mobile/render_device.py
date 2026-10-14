"""The emulator-is-starting blocks.

Eighteenth layer of the ``render_*`` split of ``render.py``.

``starting_heading`` -- "The emulator [<avd>] is still starting": ONE sentence,
two tools. Fix round 3, item 5: ``qa_mobile_test`` said this while
``qa_list_devices`` said "No devices detected" in the same turn. Both take the
words from here now. With no *avd* the text is byte-identical to what
:func:`device_pending_block` always said.

``booting_devices_block`` -- ``qa_list_devices``'s answer when nothing is listed
but an emulator this server started is still booting, or ``""``. *records* is
``emulator.recently_started()``'s list. Never raises.

``device_pending_block`` -- the emulator is starting; hand back a pointer, never
a blocked call.
"""

from __future__ import annotations

_BOOT_TAIL = (
    "A cold emulator takes a minute or two. Call `qa_list_devices` again in "
    "a few seconds; if it is still missing after several minutes, check the "
    "emulator window for an error."
)


def starting_heading(avd: str = "") -> str:
    """The emulator-is-starting sentence both device tools share."""
    name = " ".join(str(avd or "").split())[:80]
    if name:
        return "The emulator " + name + " is still starting"
    return "The emulator is still starting"


def _boot_rows(rows: list[dict]) -> list[str]:
    lines = []
    for row in rows:
        try:
            age = max(0, int(row.get("age_s") or 0))
        except (TypeError, ValueError, OverflowError):
            age = 0
        lines.append(
            "- `"
            + " ".join(str(row.get("avd") or "").split())[:80]
            + "` was started "
            + str(age)
            + " s ago and is not visible to adb yet."
        )
    return lines


def booting_devices_block(records: object) -> str:
    """The still-booting emulator list for ``qa_list_devices``, or ``""``."""
    rows = [
        r for r in (records if isinstance(records, list) else []) if isinstance(r, dict)
    ]
    rows = [r for r in rows if str(r.get("avd") or "").strip()]
    if not rows:
        return ""
    lines = ["## " + starting_heading(str(rows[-1].get("avd") or "")), ""]
    lines.extend(_boot_rows(rows))
    lines.append("")
    lines.append(_BOOT_TAIL)
    return "\n".join(lines)


def device_pending_block(state: object) -> str:
    """The emulator is starting; hand back a pointer, never a blocked call."""
    body = state if isinstance(state, dict) else {}
    return (
        "## "
        + starting_heading(str(body.get("avd") or ""))
        + "\n\n"
        + str(body.get("detail") or "")[:300]
        + "\n\nNothing is waiting on it in this call — a tool call that "
        "blocks on a boot dies at the client's timeout and tells the tester "
        "nothing. Call `qa_mobile_status` in a few seconds; when it reports the "
        "device ready, call `qa_mobile_test` again."
    )
