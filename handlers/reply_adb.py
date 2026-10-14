"""adb notices and the not-usable device section (moved from handlers._reply)."""

from __future__ import annotations

from tools.untrusted import wrap_untrusted


def _dropped_count(raw: int) -> int:
    """*raw* as a non-negative count; 0 for anything that is not a number."""
    try:
        dropped = max(0, int(raw or 0))
    except (TypeError, ValueError, OverflowError):
        # OverflowError is not decoration: `int(float("inf"))` raises it, this
        # function promises never to raise, and the value arrives from a dict
        # a caller assembled. Falling to 0 means the notice is omitted rather
        # than the whole device list being lost to a traceback.
        dropped = 0
    return dropped


# OUR PROSE, and deliberately OUTSIDE the untrusted block below, exactly
# like the remediation text: this is the sentence the tester acts on and no
# device may dress its own words as it. The parameter has a default only
# because a one-argument call is pinned; both production call sites pass it
# explicitly and a test asserts they do, so the default cannot silence it.
def _dropped_notice(dropped: int) -> str:
    """The unparsed-lines warning ("" when nothing was dropped)."""
    return (
        (
            "\n\n> \u26a0\ufe0f adb listed more transports than this server "
            "reads, so " + str(dropped) + " line(s) were not parsed. A device "
            "missing from this list may be one of them \u2014 unplug what you "
            "are not using, or run `adb kill-server`, then retry "
            "`qa_list_devices`."
        )
        if dropped
        else ""
    )


def _adb_notice(adb_dropped_lines: int, adb_problem: str) -> str:
    """The adb warnings appended to every device reply ("" when there are none)."""
    notice = _dropped_notice(_dropped_count(adb_dropped_lines))
    if adb_problem:
        # OUR sentence about adb (B4): a timed-out `adb start-server` or
        # `adb devices -l` is otherwise indistinguishable from "no devices".
        notice += "\n\n> \u26a0\ufe0f " + " ".join(str(adb_problem).split())[:400]
    return notice


def _attached_header(count: int) -> list[str]:
    """The heading and the preamble of the not-usable section."""
    return [
        "",
        f"## Detected but not usable yet ({count})",
        "",
        "These are plugged in, but this server cannot drive them until "
        "the step below is done — so they are not offered as a choice.",
        "",
    ]


def _attached_summary(attached: list) -> tuple[list[str], list[str]]:
    """The reported (serial, state) lines and the de-duplicated fixes."""
    reported: list[str] = []
    steps: list[str] = []
    for dev in attached:
        serial = str(dev.get("id") or "?")[:80]
        state = str(dev.get("raw_state") or dev.get("state") or "unknown")[:80]
        reported.append(serial + " — adb reports: " + state)
        fix = str(dev.get("remediation") or "").strip()
        if fix and fix not in steps:
            steps.append(fix)
    return reported, steps


# THE DEVICE'S OWN WORDS, WRAPPED. The serial and the state token are
# read off a USB device this server does not control, and this reply
# goes straight to the tester's chat model, so they are
# externally-sourced text under the same hard rule as fetched URLs and
# Jira content. No exemption was available: the bounded state
# vocabulary constrains `state`, NOT `raw_state` on the unknown branch
# and not `-l` metadata, so escaping alone is not the standard here.
# Wrapping also strips a device that tries to close the block itself --
# the raw-state cap is wider than the closing tag.
# OUR text, deliberately OUTSIDE the block and de-duplicated: this is
# what the tester acts on, and a device must never be able to dress its
# own words as the instruction.
def _attached_devices_lines(attached: list) -> list[str]:
    """The "Detected but not usable yet" section, one entry per output line."""
    lines = _attached_header(len(attached))
    reported, steps = _attached_summary(attached)
    lines.append(
        wrap_untrusted("attached_devices_adb", "\n".join(reported), limit=2000)
    )
    if steps:
        lines.extend(["", "**What to do**", ""])
        lines.extend("- " + step for step in steps)
    return lines
