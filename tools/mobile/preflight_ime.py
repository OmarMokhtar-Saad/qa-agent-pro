"""The keyboard steps of the preflight: pinned, oracle, and the 7/8/9 group."""

from __future__ import annotations

import logging

from tools.mobile import ime
from tools.mobile import preflight_ime_present as ime_present
from tools.mobile import preflight_rows as rows
from tools.mobile.preflight_consts import _STEP_ERRORS
from tools.mobile.preflight_rows import _Dev, _failed

logger = logging.getLogger("tools.mobile.preflight")

_ORACLE_FIX = (
    "The QA keyboard did not answer a probe. Reinstall it and select it again."
)


# 6. IME pinned (Phase 0 Part B). Returns whether it is.
def _step_ime_pinned(checks: list[dict]) -> bool:
    """6. IME pinned (Phase 0 Part B). Returns whether it is."""
    pinned = False
    try:
        status = (ime.manifest_status() or {}).get("content") or {}
        pinned = bool(status.get("ok"))
        checks.append(
            rows.record_row(
                "ime_pinned",
                rows._Row(
                    pinned,
                    str(status.get("detail") or ""),
                    str(status.get("fix") or ""),
                ),
            )
        )
    except _STEP_ERRORS as exc:
        checks.append(_failed("ime_pinned", exc))
    return pinned


async def _oracle_row(dev: _Dev) -> dict:
    """The single ``ime_oracle`` row read from the keyboard probe."""
    probed = await (dev.early.pop("ime_oracle", None) or ime.probe(dev.serial))
    if probed.get("error"):
        return rows.record_row(
            "ime_oracle",
            rows._Row(
                False, str(probed["error"]), "Reinstall the QA keyboard and select it."
            ),
        )
    body = probed["content"] or {}
    ok = bool(body.get("ok"))
    detail = "probe result=" + str(body.get("result"))
    return rows.record_row(
        "ime_oracle", rows._Row(ok, detail, "" if ok else _ORACLE_FIX)
    )


# 9. the QA keyboard answers a probe.
async def _step_ime_oracle(checks: list[dict], dev: _Dev) -> None:
    """9. the QA keyboard answers a probe."""
    try:
        if not dev.serial:
            checks.append(
                rows.record_row(
                    "ime_oracle", rows._Row(False, "cannot check without a device")
                )
            )
            return
        checks.append(await _oracle_row(dev))
    except _STEP_ERRORS as exc:
        checks.append(_failed("ime_oracle", exc))


def _not_pinned_rows(checks: list[dict]) -> None:
    """Append the three not-checked rows used when the keyboard is not pinned."""
    for name in ("ime_installed", "ime_selected", "ime_oracle"):
        checks.append(
            rows.record_row(
                name,
                rows._Row(
                    False, "not checked: " + ime.NOT_PINNED_DETAIL, ime.NOT_PINNED_FIX
                ),
            )
        )


# 7/8/9. IME installed, selected, oracle.
#
# Each is reported even when 6 failed, with the SAME reason, because a
# tester reading "1 of 10 failed" and a tester reading "4 of 10 failed,
# all for one missing release" take the same action -- and hiding three
# of them would make the count a lie.
async def _step_ime_probes(checks: list[dict], pinned: bool, dev: _Dev) -> None:
    """7/8/9. IME installed, selected, oracle."""
    if not pinned:
        _not_pinned_rows(checks)
        return
    manifest = (ime.manifest() or {}).get("content") or {}
    ime_id = str(manifest.get("ime_id") or "")
    await ime_present._step_ime_installed(checks, manifest, dev)
    await ime_present._step_ime_selected(checks, ime_id, dev)
    await _step_ime_oracle(checks, dev)
