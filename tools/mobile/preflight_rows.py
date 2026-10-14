"""Row builders of the preflight: the one record shape every check appends.

Each record is ``{name, ok, detail, fix, blocking}``; ``_unanswered`` is the
shared answer for a probe that did not answer.
"""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile.preflight_consts import ADVISORY_CHECKS, TYPING_BLOCKING_CHECKS


class _Dev(NamedTuple):
    """The resolved device: its serial and the early probes started for it."""

    serial: str
    early: dict


class _Req(NamedTuple):
    """What a run asks for: the target package and the resolved serial."""

    target_package: str
    serial: str


def _exit_code(body: object, default: int = 1) -> int:
    """A probe's exit code, with ZERO read as zero.

    ``int(body.get("rc") or 1)`` is the trap this exists to close: a successful
    command exits 0, 0 is falsy, and the fallback then turns every success into
    a failure. Written once because the same idiom was about to appear twice.
    """
    value = (body or {}).get("rc") if isinstance(body, dict) else None
    if value is None or isinstance(value, bool):
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _mark_typing_blocking(checks: list) -> None:
    """Make the typing-blocking records blocking, in place."""
    for record in checks:
        if isinstance(record, dict) and record.get("name") in TYPING_BLOCKING_CHECKS:
            record["blocking"] = True


class _Row(NamedTuple):
    """What one check found: whether it passed, the detail, and the fix to offer."""

    ok: bool
    detail: str
    fix: str = ""


def record_row(name: str, row: _Row) -> dict:
    return {
        "name": str(name),
        "ok": bool(row.ok),
        "detail": str(row.detail),
        "fix": str(row.fix),
        "blocking": str(name) not in ADVISORY_CHECKS,
    }


_UNANSWERED_FIX = (
    "Check the emulator is still up (`qa_list_devices`) and run the "
    "preflight again. Nothing is known about this check either way."
)


# The record for a probe that DID NOT ANSWER, or None when it did.
#
# ONE SHAPE, because this file has now produced the same defect three times:
# ``package_installed`` read ``adb.installed_packages(...).get("content")``
# with no look at ``error`` and printed "`pkg` is NOT installed" off a probe
# that failed, and ``ime_installed`` and ``ime_selected`` did the identical
# thing with ``ime.installed`` and ``ime.current_ime``. The same class had
# already been fixed at ``session.install_state`` (44c2be95) and
# ``render.install_menu_markdown`` (d7e0fff2), so this is the third and the
# fix is a SHAPE rather than three more patches.
#
# ``ok`` is False -- a probe that did not answer has not passed -- but the
# DETAIL says the check could not be made, so a tester is never told to
# install an app that may well be there. ``blocking`` follows the check's own
# name via :func:`record_row`, unchanged: this says nothing new about severity.
#
# ``tests/mobile/test_mobile_probe_envelopes.py`` is the other half. This
# removes the duplication inside this file; the scanner is what stops a
# FOURTH site somewhere else.
def _unanswered(name: str, result: object, fix: str = "") -> dict | None:
    """The record for a probe that did not answer, or None when it did."""
    body = result if isinstance(result, dict) else {}
    problem = str(body.get("error") or "")
    if not problem:
        return None
    detail = "could not be checked -- the device did not answer: " + problem[:200]
    return record_row(name, _Row(False, detail, fix or _UNANSWERED_FIX))


def _failed(name: str, exc: Exception) -> dict:
    """The row for a check that raised."""
    return record_row(name, _Row(False, "check failed: " + str(exc)))
