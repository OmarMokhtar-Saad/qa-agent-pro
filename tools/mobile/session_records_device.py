"""Mobile session: the device record and the tester-facing device line. Cut verbatim from tools/mobile/session_records.py (w17 sessA)."""

from __future__ import annotations

from typing import Any

from tools.mobile.session_records_locale import locale_phrase


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


def _device_detail(device: Any) -> str:
    """``model, API n`` for the device line, or ``""``."""
    return ", ".join(
        part
        for part in (
            str(device.get("model") or ""),
            ("API " + str(device.get("api"))) if device.get("api") else "",
        )
        if part
    )


def _device_text(serial: str, device: Any, kind: str) -> str:
    """The device line for a run whose record names a kind."""
    detail = _device_detail(device)
    return (
        "\n\n- Device: `"
        + serial
        + "` — "
        + ("an emulator" if kind == "emulator" else "a physical device")
        + ((" (" + detail + ")") if detail else "")
    )


# The tester-facing "what hardware was this?" line, or ``""``.
#
# ``""`` for a run with no SERIAL, and only for that: an empty line is the
# honest answer when the record does not say which device, and the alternative
# ("emulator", assumed) would be a claim about evidence nobody made.
#
# A run WITH a serial and no recorded ``kind`` -- every manifest written before
# that field existed -- takes its own branch below and renders
# "(kind not recorded for this run)". So this function does NOT render an older
# run exactly as it did before ``device.kind``, and the claim that it does was
# left standing here after the report started naming the same state: see
# ``report.DEVICE_KIND_UNKNOWN``, which is a SECOND wording of this one. The
# ``else "a physical device"`` in ``_device_text`` therefore cannot be reached by
# an unrecognised kind, because ``device_record`` writes ``""`` for one and that
# empty kind is handled here.
def device_line(resolved: object) -> str:
    """The tester-facing "what hardware was this?" line, or ``""``."""
    body = resolved if isinstance(resolved, dict) else {}
    serial = str(body.get("serial") or "")
    if not serial:
        return ""
    device: Any = body.get("device") if isinstance(body.get("device"), dict) else {}
    kind = str(device.get("kind") or "")
    if not kind:
        return (
            "\n\n- Device: `"
            + serial
            + "` (kind not recorded for this run)"
            + locale_phrase(body)
        )
    return _device_text(serial, device, kind) + locale_phrase(body)
