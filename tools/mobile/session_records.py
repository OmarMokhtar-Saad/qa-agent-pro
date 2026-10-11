"""Mobile session: locale and run records (device, capture, reset-app). Cut verbatim from tools/mobile/session.py (P4.4)."""

from __future__ import annotations

import logging

from tools.mobile import (
    adb,
)
from tools.mobile.session_core import STATE_ABANDONED

logger = logging.getLogger("tools.mobile.session")


#: ``locale.source`` -- the one sentinel this module invents for the language
#: fact, with exactly three values. Consumers: the manifest written by the two
#: planners, ``report._locale_cell`` (which branches on all three) and
#: :func:`locale_phrase` (which deliberately does NOT branch on it -- a tester
#: reading the chat line needs the language and whether it took, not how it got
#: there, and that is the right ``else`` rather than an oversight).
LOCALE_FROM_BOOT = "boot"
LOCALE_FROM_SETPROP = "setprop"
LOCALE_FROM_DEVICE = "device"


def _same_language(actual: object, wanted: object) -> bool:
    """Does *actual* satisfy the request *wanted*?

    A request WITH a region must match exactly: a tester who asked for ``ar-EG``
    and got ``ar-SA`` has different date, number and currency formats, which is
    frequently the thing under test. A request without one is satisfied by any
    region of that language. Empty on either side is never a match -- an unknown
    locale is not a satisfied one.
    """
    got = str(actual or "").strip()
    ask = str(wanted or "").strip()
    if not got or not ask:
        return False
    if "-" in ask:
        return got == ask
    return got.split("-")[0] == ask


def _locale_body(
    requested: object, actual: object, persisted: object, source: object, detail: object
) -> dict:
    """THE shape of the language record, built in one place.

    ``matched`` is a POSITIVE claim made only from a read-back that answered:
    an unanswerable probe leaves it False, so no consumer can turn "we could not
    ask" into "the run was in Arabic". Same rule, same reason, as
    ``install_state.probed``.
    """
    return {
        "requested": str(requested or ""),
        "actual": str(actual or ""),
        "persisted": str(persisted or ""),
        "source": str(source or LOCALE_FROM_DEVICE),
        "matched": (
            _same_language(actual, requested) if requested else bool(str(actual or ""))
        ),
        "detail": str(detail or "")[:200],
    }


def locale_record(body: object) -> dict:
    """Normalise a language record for the manifest. Never raises.

    The counterpart of :func:`device_record`: one shape written by the two
    planners and read by every consumer, so a reader never has to ask which of
    two spellings a manifest used.
    """
    given = body if isinstance(body, dict) else {}
    return _locale_body(
        given.get("requested"),
        given.get("actual"),
        given.get("persisted"),
        given.get("source"),
        given.get("detail"),
    )


async def apply_locale(
    serial: str, requested: str = "", *, booted_with: str = ""
) -> dict:
    """Put the device into *requested* and report what it is ACTUALLY in.

    ``{"error", "content": {requested, actual, persisted, source, matched, detail}}``

    Three outcomes, and they are deliberately not one:

    * nothing requested -> one read, and the record says what the device was
      already in (``source=device``);
    * requested and the device is already in it -> confirmed, no write;
    * requested and not -> ``adb.set_locale`` is attempted and the answer is
      whatever the device says afterwards, never the exit code.

    This function never refuses; it REPORTS. The refusal is the caller's, in
    ``mcp_handlers._mobile_locale_stage``, because only the caller knows whether
    a run is about to be started on the strength of it.
    """
    try:
        content = await _locale_content(serial, requested, booted_with)
        return {"error": None, "content": content}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.apply_locale failed")
        return {"error": str(exc), "content": None}


async def _locale_content(serial: str, requested: str, booted_with: str) -> dict:
    """The language record for one ``apply_locale`` call (may raise)."""
    wanted = str(requested or "").strip()
    seen = await adb.runtime_locale(serial)
    if seen.get("error"):
        return _locale_body(
            wanted, "", "", LOCALE_FROM_DEVICE, str(seen["error"])[:200]
        )
    actual = str(seen.get("content") or "")
    if not wanted:
        return _locale_body("", actual, "", LOCALE_FROM_DEVICE, "")
    source = (
        LOCALE_FROM_BOOT
        if str(booted_with or "").strip() == wanted
        else LOCALE_FROM_SETPROP
    )
    if _same_language(actual, wanted):
        persisted = await adb.persisted_locale(serial)
        saved = "" if persisted.get("error") else str(persisted.get("content") or "")
        return _locale_body(wanted, actual, saved, source, "")
    return await _locale_changed(serial, wanted, actual, source)


async def _locale_changed(serial: str, wanted: str, actual: str, source: str) -> dict:
    """Write *wanted* to the device and report what it says afterwards."""
    changed = await adb.set_locale(serial, wanted)
    if changed.get("error"):
        return _locale_body(wanted, actual, "", source, str(changed["error"])[:200])
    body = changed.get("content") or {}
    return _locale_body(
        wanted,
        str(body.get("runtime") or ""),
        str(body.get("persisted") or ""),
        source,
        "",
    )


def locale_phrase(resolved: object) -> str:
    """The language clause of the device line, or ``""``.

    Read from the RECORD, never from the device again: two readers asking the
    device could get two answers, and a finished run must still say what it ran
    in after the emulator is gone.
    """
    body = resolved if isinstance(resolved, dict) else {}
    record = body.get("locale") if isinstance(body.get("locale"), dict) else {}
    actual = str(record.get("actual") or "")
    requested = str(record.get("requested") or "")
    if not actual and not requested:
        return ""
    if requested and not record.get("matched"):
        return ", NOT in " + requested + (" but in " + actual if actual else "")
    return ", in " + actual if actual else ""


def capture_record(source: object) -> dict:
    """Normalise the run-level API-capture tier for the manifest. Never raises.

    THE ONE PRODUCER of the tier vocabulary is ``tools.mobile_capture.ladder``;
    this is a thin pass-through so the two planners below need no import at
    module load time and every consumer of the manifest's ``capture`` key
    reads the SAME shape ``ladder.record`` defines -- never a second
    normaliser drifting from the first.
    """
    from tools.mobile_capture import ladder

    return ladder.record(source)


def reset_app_record(body: object) -> dict:
    """Normalise the pre-run app-reset outcome for the manifest. Never raises.

    The counterpart of :func:`locale_record`/:func:`capture_record`: one
    shape written by both planners below and read by every consumer, so a
    finished run still states whether its own app's data was wiped before it
    started, after the device that ran ``pm clear`` is gone.
    """
    given = body if isinstance(body, dict) else {}
    return {
        "requested": bool(given.get("requested")),
        "package": str(given.get("package") or ""),
        "cleared": bool(given.get("cleared")),
    }


def reset_step_status(resolved: object, cases: object) -> dict:
    """What "reset" means for the relay block: requested from EITHER the
    launch-time ``reset_app=true`` flag (the manifest's ``reset_app`` record,
    see :func:`reset_app_record`) OR a ``clear_app_data`` op the model
    submitted mid-run, and done only when a clear actually landed. A
    requested reset that never confirmed is exactly the gap the relay block
    exists to surface. A run once read as a pass after a reset that never ran.
    """
    body = resolved if isinstance(resolved, dict) else {}
    manifest_reset = body.get("reset_app")
    manifest_reset = manifest_reset if isinstance(manifest_reset, dict) else {}
    requested = bool(manifest_reset.get("requested"))
    done = bool(manifest_reset.get("cleared"))
    for row in list(cases or []):
        if not isinstance(row, dict):
            continue
        for item in list(row.get("trace") or []):
            if not isinstance(item, dict):
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            if str(action.get("op") or "") != "clear_app_data":
                continue
            requested = True
            if str(item.get("outcome") or "") == "ok":
                done = True
    return {"requested": requested, "done": done}


def run_verdict(resolved: object, cases: object) -> str:
    """pass / fail / unverified -- the ONE word a tester reads to answer "did
    this run pass". An abandoned run and a requested-but-unconfirmed reset
    BOTH force ``unverified``: a run this lane cannot vouch for must never
    read as a pass, which is the defect a chat once told a tester "Login
    succeeded" against.
    """
    from tools.mobile import case_runner

    body = resolved if isinstance(resolved, dict) else {}
    rows = [row for row in list(cases or []) if isinstance(row, dict)]
    reset = reset_step_status(body, rows)
    if str(body.get("state") or "") == STATE_ABANDONED:
        return case_runner.VERDICT_UNVERIFIED
    if reset.get("requested") and not reset.get("done"):
        return case_runner.VERDICT_UNVERIFIED
    if not rows:
        return case_runner.VERDICT_UNVERIFIED
    verdicts = {str(row.get("verdict") or "") for row in rows}
    if case_runner.VERDICT_FAIL in verdicts:
        return case_runner.VERDICT_FAIL
    if verdicts <= {case_runner.VERDICT_PASS}:
        return case_runner.VERDICT_PASS
    return case_runner.VERDICT_UNVERIFIED


def device_record(facts: object) -> dict:
    """Normalise ``adb.device_facts`` content for the manifest. Never raises.

    One shape, written by the two planners and read by every consumer, so a
    reader never has to ask which of two spellings a manifest used. An unknown
    kind stays ``""`` rather than defaulting to ``emulator``: guessing here
    would put a claim about the EVIDENCE VALUE of a run into the report, and a
    confident wrong answer is worse than a blank.
    """
    body = facts if isinstance(facts, dict) else {}
    kind = str(body.get("kind") or "")
    return {
        "kind": kind if kind in ("emulator", "physical") else "",
        "model": str(body.get("model") or "")[:60],
        "api": str(body.get("api") or "")[:8],
    }


def device_line(resolved: object) -> str:
    """The tester-facing "what hardware was this?" line, or ``""``.

    ``""`` for a run with no SERIAL, and only for that: an empty line is the
    honest answer when the record does not say which device, and the alternative
    ("emulator", assumed) would be a claim about evidence nobody made.

    A run WITH a serial and no recorded ``kind`` -- every manifest written before
    that field existed -- takes its own branch below and renders
    "(kind not recorded for this run)". So this function does NOT render an older
    run exactly as it did before ``device.kind``, and the claim that it does was
    left standing here after the report started naming the same state: see
    ``report.DEVICE_KIND_UNKNOWN``, which is a SECOND wording of this one. The
    ``else "a physical device"`` below therefore cannot be reached by an
    unrecognised kind, because ``device_record`` writes ``""`` for one and that
    empty kind is handled above.
    """
    body = resolved if isinstance(resolved, dict) else {}
    serial = str(body.get("serial") or "")
    if not serial:
        return ""
    device = body.get("device") if isinstance(body.get("device"), dict) else {}
    kind = str(device.get("kind") or "")
    if not kind:
        return (
            "\n\n- Device: `"
            + serial
            + "` (kind not recorded for this run)"
            + locale_phrase(body)
        )
    detail = ", ".join(
        part
        for part in (
            str(device.get("model") or ""),
            ("API " + str(device.get("api"))) if device.get("api") else "",
        )
        if part
    )
    return (
        "\n\n- Device: `"
        + serial
        + "` — "
        + ("an emulator" if kind == "emulator" else "a physical device")
        + ((" (" + detail + ")") if detail else "")
        + locale_phrase(body)
    )
