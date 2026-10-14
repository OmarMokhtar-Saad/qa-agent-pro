"""``qa_setup_capture`` for ``mobile_device``: prepare, check or remove a
device's API-capture trust."""

from __future__ import annotations

from handlers import _common, mobile_gate
from handlers.mobile.device_requests import CapArgs
from tools import mobile_capture as api_capture
from tools.mobile import render as mobile_render
from tools.mobile_capture import ca, cert, ledger, teardown
from tools.untrusted import single_line as _safe


def _normalised(request: CapArgs) -> tuple[str, str]:
    """``(serial, action)`` of *request*, trimmed (action lower-cased)."""
    serial = str(request.serial or "").strip()
    return serial, str(request.action or "prepare").strip().lower()


def _capture_refusal(request: CapArgs) -> str:
    """The refusal for a missing serial or an unknown action, else ``""``."""

    serial, chosen = _normalised(request)
    if not serial:
        return (
            "⚠️ `serial` is required -- pass the device's adb serial. "
            "Nothing was touched."
        )
    if chosen not in ("prepare", "status", "remove"):
        return (
            "⚠️ Unknown action "
            + repr(_safe(chosen, 40))
            + " -- `action` must be one of `prepare`, `status`, `remove`. "
            "Nothing was touched."
        )
    return ""


# ``prepare`` calls the SAME ``tools.mobile_capture.prepare`` the run's own
# consent stage calls (T3.2a/T4.2a) -- this tool never re-decides
# anything, and a second, unrelated caller on the same serial is refused
# by name rather than racing the run. ``remove`` needs ``apply=true`` like
# every other device-touching step in this lane (brief decision 8: no new
# flag -- the certificate install reads the lane's kill-switch itself, in
# ``cert.install``, and ``apply`` is the per-call gate).
async def handle_setup_capture(args: CapArgs | None = None) -> str:
    """``qa_setup_capture``: prepare / check / remove a device's API-capture
    trust AHEAD of a run. Never raises."""
    if not _common.mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()

    request = args or CapArgs()
    refusal = _capture_refusal(request)
    if refusal:
        return refusal
    serial, chosen = _normalised(request)
    if chosen == "status":
        return _setup_capture_status(serial)
    if chosen == "remove":
        return await _setup_capture_remove(serial, request.apply)
    # action == "prepare"
    if not (request.apply and request.capture_ack):
        return mobile_render.capture_menu_markdown()
    return await _setup_capture_prepare(serial, request.apply)


def _tier_body(serial: str) -> dict:
    """The ledger row for *serial* under this server's certificate."""

    made = ca.ensure_ca()
    fingerprint = (
        (made.get("content") or {}).get("fingerprint") if not made.get("error") else ""
    )
    known = ledger.tier_for(serial, fingerprint or "")
    return (known.get("content") or {}) if not known.get("error") else {}


def _cert_line(serial: str) -> str:
    """Whether the certificate is installed on *serial*, and via which store."""

    cert_status = cert.status(serial).get("content") or {}
    return (
        "yes, via the " + _safe(str(cert_status.get("route") or ""), 20) + " store"
        if cert_status.get("installed")
        else "no"
    )


def _setup_capture_status(serial: str) -> str:
    """The ``status`` action's markdown for *serial*."""

    body = _tier_body(serial)
    cert_line = _cert_line(serial)
    return (
        "## Capture status for `"
        + _safe(serial, 80)
        + "`\n\n- tier: **"
        + _safe(str(body.get("tier") or "none"), 40)
        + "**\n- known under this certificate: "
        + ("yes" if body.get("known_serial") else "no")
        + "\n- certificate installed: "
        + cert_line
    )


def _removed_text(serial: str, cleared: dict, removed: dict) -> str:
    """The ``remove`` action's markdown once the proxy and certificate are gone."""

    removed_body = removed.get("content") or {}
    return (
        "## Capture removed for `"
        + _safe(serial, 80)
        + "`\n\n- proxy cleared: "
        + ("yes" if cleared.get("cleared") else "no (check status next run)")
        + "\n- certificate removed from the device: "
        + ("yes" if removed_body.get("device_removed") else "no")
        + (
            " (" + _safe(str(removed_body.get("detail") or ""), 200) + ")"
            if removed_body.get("detail")
            else ""
        )
        + "\n- ledger row forgotten: yes"
    )


async def _setup_capture_remove(serial: str, apply: bool) -> str:
    """The ``remove`` action: refused without ``apply``, else clear and remove."""
    if not apply:
        return (
            "⚠️ Removing the certificate touches the device and "
            "needs `apply=true`; nothing was removed."
        )

    cleared = await teardown.clear(serial, reason="qa_setup_capture_remove")
    removed = await cert.remove(serial, apply=True)
    return _removed_text(serial, cleared, removed)


async def _setup_capture_prepare(serial: str, apply: bool) -> str:
    """The ``prepare`` action's markdown, once consent is given."""

    prepared = await api_capture.prepare(serial, apply_=apply)
    body = (prepared.get("content") or {}) if not prepared.get("error") else {}
    tier = str(body.get("tier") or "none")
    message = str(body.get("message") or "")
    return (
        "## Capture prepared for `"
        + _safe(serial, 80)
        + "`\n\n- tier: **"
        + _safe(tier, 40)
        + "**\n"
        + (("- " + _safe(message, 300) + "\n") if message else "")
    )
