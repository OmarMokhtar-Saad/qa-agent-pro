"""The keyboard-present steps of the preflight: installed and selected."""

from __future__ import annotations

import logging

from tools.mobile import ime
from tools.mobile import preflight_rows as rows
from tools.mobile.preflight_consts import _STEP_ERRORS
from tools.mobile.preflight_rows import _Dev, _failed, _unanswered

logger = logging.getLogger("tools.mobile.preflight")

_INSTALL_FIX = (
    "This does not block a run. The mobile lane "
    "installs the QA keyboard itself at the start of "
    "a run whose script types or clears text."
)
_SELECT_FIX = (
    "The mobile lane selects the QA keyboard "
    "itself, and restores yours when the run ends."
)


async def _installed_row(manifest: dict, dev: _Dev) -> dict:
    """The single ``ime_installed`` row read from the installed-package probe."""
    present = (
        await (dev.early.pop("ime_installed", None) or ime.installed(dev.serial))
    ) or {}
    # `ime.installed` propagates `adb.installed_packages`'s
    # envelope verbatim (ime.py:197-198), so the evidence is
    # here and used to be dropped: a failed probe rendered
    # "is NOT installed" with a fix line about the next run.
    unanswered = _unanswered("ime_installed", present)
    if unanswered:
        return unanswered
    ok = bool((present.get("content") or {}).get("installed"))
    detail = str(manifest.get("package")) + (
        " is installed" if ok else " is NOT installed"
    )
    return rows.record_row(
        "ime_installed", rows._Row(ok, detail, "" if ok else _INSTALL_FIX)
    )


# 7. the QA keyboard is installed.
async def _step_ime_installed(checks: list[dict], manifest: dict, dev: _Dev) -> None:
    """7. the QA keyboard is installed."""
    try:
        if not dev.serial:
            checks.append(
                rows.record_row(
                    "ime_installed",
                    rows._Row(
                        False,
                        "cannot check without a device",
                        "Start the emulator first.",
                    ),
                )
            )
            return
        checks.append(await _installed_row(manifest, dev))
    except _STEP_ERRORS as exc:
        checks.append(_failed("ime_installed", exc))


async def _selected_row(ime_id: str, dev: _Dev) -> dict:
    """The single ``ime_selected`` row read from the current input method."""
    current = await (dev.early.pop("ime_selected", None) or ime.current_ime(dev.serial))
    # THE SAME HOLE AS THE TWO NEIGHBOURS: `ime.current_ime` returns its
    # adb envelope verbatim (ime.py:264-265), and reading only `content`
    # made a failed probe render "active input method: (none)".
    unanswered = _unanswered("ime_selected", current)
    if unanswered:
        return unanswered
    active = str(current.get("content") or "")
    # By component identity: Android reports `pkg/.Class` and
    # the manifest pins `pkg/pkg.Class`. `==` refused every run
    # on a correctly configured device (2026-09-04, live).
    ok = ime.same_component(active, ime_id)
    detail = "active input method: " + (active or "(none)")
    return rows.record_row(
        "ime_selected", rows._Row(ok, detail, "" if ok else _SELECT_FIX)
    )


# 8. the QA keyboard is the selected input method.
async def _step_ime_selected(checks: list[dict], ime_id: str, dev: _Dev) -> None:
    """8. the QA keyboard is the selected input method."""
    try:
        if not dev.serial:
            checks.append(
                rows.record_row(
                    "ime_selected", rows._Row(False, "cannot check without a device")
                )
            )
            return
        checks.append(await _selected_row(ime_id, dev))
    except _STEP_ERRORS as exc:
        checks.append(_failed("ime_selected", exc))
