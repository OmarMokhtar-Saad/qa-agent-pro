"""The host's way on after any mobile tool stops (fix/1490, every app, every case).

A server step that cannot go on (an unknown screen, a page that needs the
tester's choice, a refused install, a run that is gone) used to end in a reply
that only said what went wrong. The host -- the chat model -- then had no tool
path left, and a Cursor chat sat stuck on an App Tester consent page.

So every stop reply of a mobile tool ends with the host's options, all through
this server's own tools: call again with changed arguments, drive the screen
itself as a run, or ask the tester. The host thinks; the server does what the
host then asks. The block names no app, package or screen of any one app.
"""

from __future__ import annotations

import re

#: A reply that already carries this (App Tester's own ``host_next``) is left as is.
MARK = "**You decide the next step"

_STOP_HEADS = (
    "⚠️",
    "❌",
    "Not started:",
    "update_app: failed",
    "update_app: unverified",
)

#: A case verdict line (``❌ `TC-1` title — **fail**``): the test's outcome, not a stop.
_VERDICT = re.compile(r"\S+ `[^`\n]*`[^\n]* — \*\*")

_WARN = "⚠"

_ASK = (
    "ask the tester when the choice is theirs (sign-in, payment, which app "
    "or device; App Tester's consent page the server ticks itself), and "
    "never answer such a page without their yes. "
    "Never use raw adb."
)


def _drive(device_id: str) -> str:
    """The call that opens the app on screen as a run the host drives itself."""
    device = device_id or "<device>"
    return (
        '`qa_mobile_test(device_id="' + device + '", source="installed_package", '
        'package="<app under test, not the installer>", apply=true, '
        'goal="<what to do>")`, then '
        "`qa_submit_mobile_step` per screen and `qa_mobile_stop` at the end"
    )


def _again(tool: str) -> str:
    """The option to call *tool* again with what was missing or wrong."""
    if tool == "qa_update_app":
        return "call `qa_update_app` with another `source`"
    return "call `" + tool + "` again with the missing or corrected argument"


def _step_options(tool: str, device_id: str) -> str:
    """A step that could not go on: read, resend, start over, or ask."""
    return (
        "(1) read the screen in this reply or with `qa_mobile_status(run_id=...)`; "
        "(2) send `qa_submit_mobile_step` again with another target or fewer "
        "actions; (3) end with `qa_mobile_stop` and start over with "
        "`qa_mobile_test`; (4) " + _ASK
    )


def _status_options(tool: str, device_id: str) -> str:
    """A status lookup that failed: list the runs, then name one by run id."""
    return (
        "(1) `qa_mobile_status()` with no run id lists the runs; (2) call "
        "`qa_mobile_status(run_id=...)` with a run id it shows; (3) " + _ASK
    )


def _runs_options(tool: str, device_id: str) -> str:
    """A stop that failed: list the runs, then name one."""
    return (
        "(1) `qa_mobile_status()` with no run id lists the runs; (2) call `"
        + tool
        + "` again with the run id or device serial it shows; (3) "
        + _ASK
    )


def _store_options(tool: str, device_id: str) -> str:
    """A saved-data tool that refused: call again, or ask."""
    return "(1) " + _again(tool) + ' (`action="list"` shows what is saved); (2) ' + _ASK


def _device_options(tool: str, device_id: str) -> str:
    """Any device tool: call again, drive the screen as a run, or ask."""
    return (
        "(1) "
        + _again(tool)
        + "; (2) drive the screen yourself: "
        + _drive(device_id)
        + "; (3) "
        + _ASK
    )


_BY_TOOL = {
    "qa_submit_mobile_step": _step_options,
    "qa_mobile_stop": _runs_options,
    "qa_mobile_status": _status_options,
    "qa_mobile_notes": _store_options,
    "qa_mobile_knowledge": _store_options,
    "qa_mobile_flows": _store_options,
}


def _options(tool: str, device_id: str) -> str:
    """The numbered options for *tool*'s stop; any other tool is a device tool."""
    return _BY_TOOL.get(tool, _device_options)(tool, device_id)


def _head(text: str) -> str:
    """*text* without its leading ``ℹ️`` note paragraphs (a heavy-AVD note)."""
    parts = str(text or "").lstrip().split("\n\n")
    while len(parts) > 1 and parts[0].startswith("ℹ️"):
        parts.pop(0)
    return "\n\n".join(parts).lstrip()


def is_stop(text: str) -> bool:
    """True when *text* is a stop reply that has no way on yet."""
    head = _head(text)
    if not head.startswith(_STOP_HEADS) or _VERDICT.match(head):
        return False
    return MARK not in str(text)


def as_note(text: str) -> str:
    """*text* with a leading ``⚠️`` made ``ℹ️``: a note above a run that goes on."""
    if not text.startswith(_WARN):
        return text
    return "ℹ️" + text[len(_WARN) :].lstrip("️")


def with_way_on(tool: str, text: str, device_id: str = "") -> str:
    """*text*, plus the host's options when it is a stop reply of *tool*."""
    if not isinstance(text, str) or not is_stop(text):
        return text
    return (
        text.rstrip()
        + "\n\n"
        + MARK
        + "; the server could not go on by itself.** Pick one: "
        + _options(tool, device_id)
    )
