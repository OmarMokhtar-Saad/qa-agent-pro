"""Every tester-facing string the mobile lane prints, as pure functions.

Why a separate module from ``session``: the state machine decides, this renders,
and neither can be tested by accident through the other. Nothing here reads
settings, touches the filesystem or awaits a device, so every string in the lane
is reachable from a unit test without an emulator -- which is what makes the
"never raw XML in a packet" and "every menu option has a mapping line"
properties checkable at all.

Two rules this module exists to keep:

1. **The menu derives its own numbering.** ``source_menu_markdown`` says "EXACTLY
   these N options" and then lists them, so N, the numbers and the mapping
   paragraph are all computed from :data:`MOBILE_SOURCES`. A hand-written count
   is how a seventh source becomes unreachable while the text still claims six.
2. **A packet is rendered, never rebuilt.** ``packet_block`` embeds the dict
   ``agents/mobile_run`` produced and adds nothing to it. The pruned screen is
   already the only screen representation in there; the raw uiautomator XML has
   no route into this file.
"""

from __future__ import annotations

import json

#: The start-menu sources, in menu order: ``(key, line)``. The key is what a
#: handler branches on and what the mapping paragraph names, so the two cannot
#: drift apart.
MOBILE_SOURCES: tuple[tuple[str, str], ...] = (
    ("current_suite", "Run the test suite from THIS chat (the one just generated)"),
    ("stored_suite", "Run a suite this install already stored"),
    (
        "own_cases",
        "Run my own cases (paste a markdown table, or give a .csv/.xlsx path)",
    ),
    ("explore", "Explore freely towards a goal I describe (no test cases needed)"),
    ("rerun_failures", "Re-run only the cases that failed in the last run"),
    ("resume", "Resume a run that is already in progress"),
)

#: How the app under test gets onto the emulator. Every one of these needs
#: ``apply=true``; the first two are the only ones that write to the device from
#: here, the middle two hand the tester the emulator's own Play Store UI.
INSTALL_SOURCES: tuple[tuple[str, str], ...] = (
    ("local_apk", "Install an .apk from a path on this machine"),
    ("download_url", "Open a download link inside the emulator's browser"),
    ("app_tester", "Open Firebase App Tester through the emulator's Play Store"),
    ("play_store", "Open the Play Store inside the emulator and install from there"),
    (
        "installed_package",
        "Use an app that is already installed (give its package name)",
    ),
)

_COUNT_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
}

FLAG_NAME = "QA_MOBILE_RUN_ENABLED"

#: The one place the packet tells the model not to hand the screen back. It is a
#: real cost: a 150-element screen echoed into the next turn doubles the tokens
#: the tester pays for and adds nothing the server does not already hold.
NO_ECHO = (
    "Do NOT echo the screen back, do not summarise it, and do not re-fetch a "
    "packet you already hold. Answer with the JSON the schema asks for and "
    "nothing else."
)


def _count_word(number: int) -> str:
    return _COUNT_WORDS.get(int(number), str(int(number)))


def _numbered(options: tuple[tuple[str, str], ...]) -> str:
    return "".join(
        str(index) + ". " + line + "\n" for index, (_key, line) in enumerate(options, 1)
    )


def source_labels() -> list[str]:
    """The menu lines, for an elicitation dialog's option list."""
    return [line for _key, line in MOBILE_SOURCES]


def source_for_label(label: str) -> str:
    """The source KEY for a dialog label, or ``""``.

    A dialog returns the label it displayed, so the mapping back to a key has to
    live next to the labels rather than in the handler -- the shape that let a
    device picker return a name no branch recognised.
    """
    wanted = str(label or "").strip()
    for key, line in MOBILE_SOURCES:
        if line == wanted or key == wanted:
            return key
    return ""


def source_for_number(value: object) -> str:
    """The source KEY for a 1-based menu number, or ``""``."""
    try:
        index = int(str(value).strip())
    except (TypeError, ValueError):
        return ""
    if 1 <= index <= len(MOBILE_SOURCES):
        return MOBILE_SOURCES[index - 1][0]
    return ""


def source_menu_markdown() -> str:
    """The start menu as an instruction to the HOST assistant.

    Same shape and same reason as ``mcp_handlers._tc_source_menu_markdown``:
    editors render a structured multiple-choice question reliably and MCP
    elicitation dialogs do not (a Cursor dialog arrives collapsed and required),
    so the menu is the product and the dialog is the optimisation.
    """
    number = {key: index for index, (key, _line) in enumerate(MOBILE_SOURCES, 1)}
    return (
        "## Ask the user: what should the emulator run?\n\n"
        "Present EXACTLY these "
        + _count_word(len(MOBILE_SOURCES))
        + " options to the user as a multiple-choice question (use your "
        "ask-user/questions UI, not prose), then follow the mapping below. Do "
        "not invent different options and do not pick one for them.\n\n"
        + _numbered(MOBILE_SOURCES)
        + "\nAfter the user picks, call `qa_mobile_test` again with `source` set "
        "to the matching key: "
        + ", ".join(
            str(number[key]) + " -> `" + key + "`" for key, _line in MOBILE_SOURCES
        )
        + ". Option "
        + str(number["stored_suite"])
        + " also needs `suite_id`, option "
        + str(number["own_cases"])
        + " needs the table or path in `cases`, option "
        + str(number["explore"])
        + " needs `goal`, and option "
        + str(number["resume"])
        + " needs `run_id`."
    )


def install_menu_markdown(package: str = "") -> str:
    """The install-source menu, with the same derived numbering discipline."""
    number = {key: index for index, (key, _line) in enumerate(INSTALL_SOURCES, 1)}
    head = "## The app under test is not on the emulator yet\n\n"
    if package:
        head = "## `" + str(package)[:80] + "` is not installed on the emulator\n\n"
    return (
        head
        + "Ask the user how they want it installed -- present EXACTLY these "
        + _count_word(len(INSTALL_SOURCES))
        + " options as a multiple-choice question:\n\n"
        + _numbered(INSTALL_SOURCES)
        + "\nThen call `qa_mobile_test` with `source` set to the matching key ("
        + ", ".join(
            str(number[key]) + " -> `" + key + "`" for key, _line in INSTALL_SOURCES
        )
        + "), the value in `app` (a path, a URL or a package name) and "
        "`apply=true`. Nothing is installed, downloaded or opened without "
        "`apply=true` -- ask the user first, on their turn, not on this one."
    )


def install_source_for_number(value: object) -> str:
    try:
        index = int(str(value).strip())
    except (TypeError, ValueError):
        return ""
    if 1 <= index <= len(INSTALL_SOURCES):
        return INSTALL_SOURCES[index - 1][0]
    return ""


def flag_refusal(step: str) -> str:
    """The refusal when the lane's kill-switch is off and ``apply=true`` was sent.

    Same three beats as ``handle_push_suite``'s: what did NOT happen, the exact
    flag plus the restart, and the reversible alternative. It refuses BY NAME
    rather than quietly doing a dry run, because a success-shaped reply for a
    step that never ran is the worse failure.
    """
    return (
        "⚠️ **Nothing happened on the device.** "
        + str(step or "That step")
        + " needs `"
        + FLAG_NAME
        + "=true` in `.env` and an MCP server restart (quit and reopen the "
        "editor). Re-run without `apply` for a preview of exactly what it "
        "would do."
    )


def apply_refusal(step: str, detail: str = "") -> str:
    """The preview a tester gets when the flag is ON and ``apply`` is not."""
    return (
        "\U0001f50e **Preview — nothing was changed.** "
        + str(step or "That step")
        + " would run"
        + ((": " + str(detail)[:300]) if detail else "")
        + ".\n\nRe-call the same way with `apply=true` once the tester has said "
        "go, on their turn."
    )


def provisioning_line(progress: object) -> str:
    """One line for the detached provisioner's state. Never a stack trace."""
    body = progress if isinstance(progress, dict) else {}
    if not body:
        return "Provisioning has not started yet."
    if body.get("error"):
        return "Provisioning stopped: " + str(body["error"])[:300]
    return (
        "Provisioning "
        + str(body.get("phase") or "running")
        + " — "
        + str(int(body.get("pct") or 0))
        + "% — "
        + str(body.get("message") or "")[:200]
    )


def preflight_block(content: object, rendered: str = "") -> str:
    """The preflight checks, failures first, each with its fix.

    *rendered* is ``preflight.render``'s output, passed in rather than imported
    so this module stays free of internal imports and the checks have exactly
    one renderer.
    """
    body = content if isinstance(content, dict) else {}
    failing = list(body.get("failing") or [])
    head = (
        "## Preflight — all clear\n\n"
        if body.get("ok")
        else "## Preflight — " + str(len(failing)) + " check(s) must be fixed first\n\n"
    )
    tail = (
        ""
        if body.get("ok")
        else "\n\nFix the ❌ items above and call `qa_mobile_test` again. "
        "Nothing runs until every check passes."
    )
    return head + (str(rendered) or "(no checks were produced)") + tail


def packet_block(packet: object, *, session_token: str = "") -> str:
    """The packet as a fenced JSON block plus the no-echo instruction.

    The packet is embedded exactly as ``agents/mobile_run`` built it. Nothing is
    added to it here -- in particular no screen, no dump and no path -- so the
    compactness property is a property of that builder and this renderer
    together, and a test can assert it over the rendered reply.
    """
    body = packet if isinstance(packet, dict) else {}
    payload = dict(body)
    if session_token:
        payload["session_token"] = str(session_token)
    try:
        text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    except Exception:  # pragma: no cover - defensive
        text = "{}"
    return "```json\n" + text + "\n```\n\n" + NO_ECHO


def verdict_line(case: object) -> str:
    """ONE line per case. A run of 200 cases is 200 lines, not 200 sections."""
    body = case if isinstance(case, dict) else {}
    marks = {
        "pass": "✅",
        "fail": "❌",
        "blocked": "⛔",
        "unverified": "⚠️",
        "needs_tester": "❓",
        "needs_model": "\U0001f501",
    }
    # Every field here arrives in the model's submitted step and is replayed
    # from disk in a later chat -- the same channel as the status block, and
    # `tc_id` sits in the same single backticks that a planted fence escaped.
    verdict = _field(body.get("verdict") or body.get("status"))
    reason = _field(body.get("reason"))[:160]
    return (
        marks.get(verdict, "•")
        + " `"
        + _field(body.get("tc_id"), "?")
        + "` "
        + _field(body.get("title"))[:80]
        + " — **"
        + (verdict or "unknown")
        + "**"
        + ((" — " + reason) if reason else "")
    )


def gate_block(point: object) -> str:
    """The soft "continue?" gate. Asks the TESTER, never assumes."""
    body = point if isinstance(point, dict) else {}
    return (
        "## "
        + str(int(body.get("done") or 0))
        + " of "
        + str(int(body.get("total") or 0))
        + " cases done — keep going?\n\n"
        + str(len(body.get("failed") or []))
        + " failed so far. Ask the tester whether to continue, and only if they "
        "say yes call `qa_mobile_test` again with the same `run_id` and "
        "`continue_run=true`. Stopping here loses nothing: every case done is "
        "already checkpointed and the run resumes from this point in any chat."
    )


# Every value below comes off DISK, and session.py persists model-supplied
# goal/package/serial into that manifest. A run resumes in any chat from its id,
# so text planted by one chat is read by another chat's model: this is a
# cross-chat injection channel, not merely display text.
_MD_BREAKOUT = str.maketrans({"`": "'", "\n": " ", "\r": " ", "\t": " "})
_MAX_FIELD_CHARS = 120


def _field(value: object, fallback: str = "") -> str:
    """A disk-sourced value, safe to interpolate into markdown.

    Backticks become apostrophes so a value cannot close the code span it is
    rendered in -- a planted fence did exactly that -- newlines collapse so it
    cannot begin a block, and the whole thing is capped. Never raises.
    """
    try:
        # translate() already maps newline, return and tab to a space, so the
        # only work left here is the BOUND: these values are model-supplied and
        # replayed from disk, and an unbounded one pushes the rest of the
        # report off a tester's screen.
        text = str(value if value is not None else "").translate(_MD_BREAKOUT)
        return text.strip()[:_MAX_FIELD_CHARS] or fallback
    except Exception:  # pragma: no cover - defensive
        return fallback


def status_block(resolved: object) -> str:
    """What ``qa_mobile_status`` prints: where the run is, from disk only."""
    try:
        body = resolved if isinstance(resolved, dict) else {}
        lines = [
            "## Mobile run `" + _field(body.get("run_id"), "?") + "`",
            "",
            # 'unknown', not '?': a question mark here reads as a rendering
            # artifact, and a tester cannot tell an odd state from a state the
            # server could not read. The two lines below already spell theirs
            # out, so this keeps the block consistent.
            "- state: **" + _field(body.get("state"), "unknown") + "**",
            "- lane: " + _field(body.get("lane"), "unknown"),
            "- app: `" + _field(body.get("package"), "(none)") + "`",
            "- device: " + _field(body.get("serial"), "(not attached)"),
        ]
        total = int(body.get("total") or 0)
        if total:
            lines.append(
                "- cases: "
                + str(int(body.get("done") or 0))
                + " done, "
                + str(len(body.get("failed") or []))
                + " failed, "
                + str(max(0, total - int(body.get("done") or 0)))
                + " remaining of "
                + str(total)
            )
        holder = str(body.get("holder") or "")
        if holder:
            lines.append("- lease: held by session `" + holder[:40] + "`")
        if str(body.get("state") or "") == "abandoned":
            # Deliberately NOT the takeover wording: no other chat holds this
            # run, so "taken over" would send a tester looking for a session
            # that does not exist. What they need is the one call that picks
            # it back up, spelled out.
            lines += [
                "",
                "**Nothing has driven this run for "
                + str(int(float(body.get("lease_age") or 0)))
                + "s, so it looks abandoned.** Its finished cases are safe on "
                "disk. To pick it up, call `qa_mobile_test` with "
                '`run_id="' + _field(body.get("run_id"), "?") + '"` and '
                "`continue_run=true`.",
            ]
        explore = body.get("explore")
        if isinstance(explore, dict) and explore:
            lines.append(
                "- exploring: turn "
                + str(int(explore.get("turn") or 0))
                + " of "
                + str(int(explore.get("turns_budget") or 0))
                + (
                    " — stopped: " + str(explore.get("stop"))
                    if explore.get("stop")
                    else ""
                )
            )
        return "\n".join(lines)
    except Exception:  # pragma: no cover - defensive
        return "## Mobile run\n\n- state: **unknown**"


def busy_block(run_id: str, tc_id: str = "") -> str:
    """The bounded-call reply: this call stopped ITSELF, and nothing was lost.

    A client kills a tool call at around 50 seconds, and a killed call looks
    exactly like a broken server -- so the lane answers before the client's
    timer does. The wording has one job beyond politeness: to say that no work
    was half-done, because a tester who thinks a case ran will not re-run it.
    """
    run = str(run_id)[:64]
    return (
        "## Still working on run `"
        + run
        + "`\n\nThis call stopped short of its own time budget so your editor "
        "would not time it out. There is no step in this reply and nothing was "
        "half-done"
        + (" \u2014 `" + str(tc_id)[:16] + "` has not started yet" if tc_id else "")
        + ".\n\n- `qa_mobile_status` shows where the run stands.\n"
        '- Call `qa_mobile_test` again with `run_id="'
        + run
        + '"` to carry on from the same place.'
    )


def report_line(
    path: str = "",
    *,
    partial: bool = False,
    opened: bool = False,
    error: str = "",
) -> str:
    """The ONE place the HTML report is described to a tester.

    Three shapes and no fourth: a path was written, a render was attempted and
    failed, or none was attempted. It never names a `.html` file that does not
    exist -- the failure mode the text this replaced was carefully avoiding, and
    the reason its replacement is a rewrite rather than an edit.
    """
    if error:
        return (
            "⚠️ The HTML report could not be written: "
            + str(error)[:300]
            + " Every verdict above is read straight from the run's own "
            "checkpoint files and is unaffected."
        )
    if not path:
        return (
            "No HTML report was written for this call. Ask for one at any time "
            "with `qa_mobile_status` and `report_now=true`: it is built from the "
            "run's own checkpoint files, so a mid-run report works too."
        )
    return (
        ("Partial report" if partial else "Report")
        + " — `"
        + str(path)[:400]
        + "`\n\n"
        + (
            "It covers the cases finished so far; ask again later for the rest. "
            if partial
            else ""
        )
        + (
            "It was opened in your browser. "
            if opened
            else "Open it in a browser: it is one self-contained file with no "
            "external assets, so it works offline. "
        )
        + "Every screen in it is composed from the element list the "
        "server already held — no screenshot is ever taken of the emulator."
    )


def summary_block(
    cases: object,
    *,
    run_id: str = "",
    partial: bool = False,
    abandoned: bool = False,
    report_path: str = "",
    report_opened: bool = False,
    report_error: str = "",
) -> str:
    """The end-of-run (or mid-run) summary, built from checkpoints only.

    One line per case, so a 200-case run is 200 lines rather than 200 sections.
    The tail delegates to :func:`report_line`, which is the only thing in this
    module that may mention the HTML report at all.
    """
    rows = [row for row in list(cases or []) if isinstance(row, dict)]
    tally: dict[str, int] = {}
    for row in rows:
        key = str(row.get("verdict") or row.get("status") or "unknown")
        tally[key] = tally.get(key, 0) + 1
    head = (
        "## Mobile run "
        + ("abandoned" if abandoned else ("progress" if partial else "finished"))
        + (" — `" + str(run_id) + "`" if run_id else "")
        + "\n\n"
    )
    counts = ", ".join(str(count) + " " + name for name, count in sorted(tally.items()))
    body = (counts or "no cases recorded yet") + "\n\n"
    lines = "\n".join(verdict_line(row) for row in rows)
    tail = "\n\n" + report_line(
        report_path, partial=partial, opened=report_opened, error=report_error
    )
    return head + body + lines + tail


def takeover_block(message: str) -> str:
    """Wrap ``run_store.takeover_message`` and stop this chat producing packets.

    The wording is deliberately careful: the lease is read-decide-write with a
    compare-after-swap that NARROWS the race rather than closing it, so this
    says the other chat holds the run and that this one has stopped -- it does
    not claim the other chat cannot be displaced in turn.
    """
    return (
        str(message)
        + "\n\nNo further packets are produced in this chat for that run. If the "
        "other chat is gone, call `qa_mobile_test` with the same `run_id` and "
        "no `session_token` to take it back."
    )


def device_pending_block(state: object) -> str:
    """The emulator is starting; hand back a pointer, never a blocked call."""
    body = state if isinstance(state, dict) else {}
    return (
        "## The emulator is still starting\n\n"
        + str(body.get("detail") or "")[:300]
        + "\n\nNothing is waiting on it in this call — a tool call that "
        "blocks on a boot dies at the client's timeout and tells the tester "
        "nothing. Call `qa_mobile_status` in a few seconds; when it reports the "
        "device ready, call `qa_mobile_test` again."
    )


def device_busy_block(refusal: object) -> str:
    """Another run holds the emulator. Say WHO, and say how to get it.

    A refusal that does not name a way forward is a dead end, and this one has
    two: take that run over (which is what makes the holder let go), or wait for
    it to finish. It deliberately does NOT offer to break the lock -- a lock
    broken under a live holder is two chats driving one device, which is the
    defect this whole mechanism exists to prevent.

    ONE lock covers the whole lane rather than one per serial, and the reason is
    stated here rather than hidden: the device stage is what picks, boots and
    provisions the device, so there is no serial to key a lock on until after
    the most contended step has already run. A tester with two devices is
    serialised across both; that is a known cost, not a bug.
    """
    from tools.mobile import run_store

    body = refusal if isinstance(refusal, dict) else {}
    who = str(body.get("holder") or "").strip()
    same = bool(body.get("same_process"))
    # `holder` IS AN OWNER LABEL, NEVER PROSE. A caller once passed a refusal
    # REASON here ("held by mrun-...") and this block dutifully told the tester
    # to call `qa_mobile_test` with `run_id="held by mrun-..."` -- an
    # instruction that cannot work. Rather than trusting every present and
    # future call site to pass the right thing, the takeover branch is entered
    # only for a label that IS a run id; anything else falls to the generic
    # line, which asks for nothing the tester cannot do.
    if (
        who
        and not who.startswith("provisioning:")
        and not run_store.looks_like_a_run_id(who)
    ):
        # THE GRAMMAR, not `valid_run_id`. That one asks whether a string is
        # safe as a path segment and says yes to every single-token status
        # string in this lane -- `handoff_failed`, `already_held`, `not_held` --
        # so it would have let one of them through as something a tester was
        # told to pass back. Two different questions, two predicates.
        who = ""
    if str(body.get("reason") or "") == "no_lock_facility":
        return (
            "## The emulator lane cannot guarantee one run at a time here\n\n"
            "This platform offers neither `fcntl` nor `msvcrt`, so nothing can "
            "stop a second chat driving the same device — and two runs on one "
            "emulator produce two reports that each describe a run that did not "
            "happen as recorded. Refusing is the safe answer; nothing was "
            "started."
        )
    lines = ["## Another run is using the emulator\n"]
    if who.startswith("provisioning:"):
        lines.append(
            "A run is being set up on this device right now"
            + (" in this same server" if same else " by another chat")
            + " — the emulator is being picked, booted or the app installed. "
            "That step finishes within the call that started it, so call "
            "`qa_mobile_test` again in a moment."
        )
    elif who:
        lines.append(
            "Run `" + who + "` holds it. Two options, and nothing here will "
            "break its hold:\n\n"
            "1. **Take that run over** — call `qa_mobile_test` with "
            '`run_id="' + who + '"` and no `session_token`. The chat that '
            "holds it lets the device go within about half a minute of losing "
            "the run, so a retry straight after may still be refused once.\n"
            "2. **Wait for it to finish** — `qa_mobile_status` with that run id "
            "shows where it is."
        )
    else:
        lines.append(
            "Another process on this machine holds it. `qa_mobile_status` lists "
            "the runs this install knows about; taking one over with "
            "`qa_mobile_test run_id=...` is what releases the device."
        )
    lines.append(
        "\nOne lock covers the whole lane, not one per device: the emulator is "
        "chosen and booted before any serial exists, so there is nothing to key "
        "a per-device lock on at the moment it matters most."
    )
    return "\n".join(lines)
