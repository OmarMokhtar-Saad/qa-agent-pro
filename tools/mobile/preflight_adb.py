"""The adb steps of the preflight: it answers, it is first on PATH, virtualization."""

from __future__ import annotations

import logging

from tools.mobile import adb, emulator, platform_info
from tools.mobile import preflight_rows as rows
from tools.mobile.preflight_consts import _STEP_ERRORS
from tools.mobile.preflight_rows import _failed

logger = logging.getLogger("tools.mobile.preflight")


def _adb_listed(checks: list[dict], listed: dict) -> list[str]:
    """Append the ``adb_responds`` row for the device listing; return the serials."""
    if listed.get("error"):
        checks.append(
            rows.record_row(
                "adb_responds",
                rows._Row(
                    False,
                    str(listed["error"]),
                    "Install Android Studio, then try again.",
                ),
            )
        )
        return []
    serials = list(listed.get("content") or [])
    detail = "adb reports " + str(len(serials)) + " device(s)"
    checks.append(rows.record_row("adb_responds", rows._Row(True, detail)))
    return serials


async def _step_adb_responds(checks: list[dict]) -> list[str]:
    """2. adb responds. Returns the attached serials (empty on any failure)."""
    serials: list[str] = []
    try:
        serials = _adb_listed(checks, await adb.devices())
    except _STEP_ERRORS as exc:
        checks.append(_failed("adb_responds", exc))
    return serials


def _path_detail(path_state: dict, shadowed: str) -> str:
    """What the ``adb_first_on_path`` row says about the adb in use."""
    moved = (
        "; a different adb (" + shadowed + ") was earlier on "
        "PATH and has been moved behind it for this process"
        if shadowed
        else ""
    )
    return "using " + str(path_state.get("adb") or "adb") + moved


def _path_fix(shadowed: str) -> str:
    """The remedy for a shadowing adb, or nothing when none shadows."""
    if not shadowed:
        return ""
    return (
        "Two adb versions on one machine fight over the adb "
        "server. Consider removing " + shadowed + " from PATH."
    )


def _path_row(path_state: dict) -> dict:
    """The single ``adb_first_on_path`` row."""
    shadowed = str(path_state.get("shadowed_by") or "")
    detail = _path_detail(path_state, shadowed)
    return rows.record_row(
        "adb_first_on_path", rows._Row(True, detail, _path_fix(shadowed))
    )


def _step_adb_first_on_path(checks: list[dict]) -> None:
    """3. adb first on PATH."""
    try:
        path_state = (emulator.ensure_adb_first_on_path() or {}).get("content") or {}
        checks.append(_path_row(path_state))
    except _STEP_ERRORS as exc:
        checks.append(_failed("adb_first_on_path", exc))


def _virtualization_row() -> dict:
    """The ``virtualization`` row; an UNDETERMINED answer is made advisory."""
    virt = (platform_info.virtualization() or {}).get("content") or {}
    virt_record = rows.record_row(
        "virtualization",
        rows._Row(
            bool(virt.get("ok")),
            str(virt.get("detail") or ""),
            str(virt.get("fix") or ""),
        ),
    )
    if virt.get("undetermined"):
        virt_record["blocking"] = False
    return virt_record


# 4a. virtualization (an EMULATOR question).
#
# Asked only when an emulator may be involved: no serial at all (one may
# have to be started) or an `emulator-*` serial. A physical serial skips
# it, because the Windows probe needs an elevated shell and used to
# refuse a run that never touched a hypervisor. An UNDETERMINED answer
# (the query itself failed) is advisory, never a refusal.
def _step_virtualization(checks: list[dict], resolved_serial: str) -> None:
    """4a. virtualization (an EMULATOR question)."""
    try:
        if not resolved_serial or str(resolved_serial).startswith("emulator-"):
            checks.insert(0, _virtualization_row())
    except _STEP_ERRORS as exc:
        checks.insert(0, _failed("virtualization", exc))
