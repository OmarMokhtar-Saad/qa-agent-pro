"""Moved from handlers (refactor P2): mobile_device."""

from __future__ import annotations

import json
import logging

from handlers import _common, _reply, mobile_gate, mobile_run, mobile_step
from tools.device_manager import list_devices
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mcp_handlers")


async def handle_mobile_stop(
    target: str = "", *, choose: _common.ChooseCb = None, **_elicitors
) -> str:
    """``qa_mobile_stop``: stop ONE run, named by run id or device serial.

    ``stop_run.stop`` decides; a target that names no single run is asked,
    never guessed. Never raises."""
    if not _common._mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        from tools.mobile import app_tools, stop_run
        from tools.mobile.session_finish import stop_ports

        result = await stop_run.stop(
            str(target or "").strip() or None,
            stop_ports(),
            mobile_run._mobile_ask(choose),
        )
    except mobile_step._MOBILE_TOOL_FAILURES as exc:
        return mobile_step._mobile_tool_failed(exc, "qa_mobile_stop")
    if result.ok:
        mobile_step._mobile_learn_after(str(getattr(result, "run_id", "") or ""))
    head = "Stopped." if result.ok else "\u26a0\ufe0f Not stopped."
    return (
        head
        + "\n"
        + wrap_untrusted("mobile_stop", result.message, app_tools.MAX_TOOL_TEXT_CHARS)
    )


async def handle_app_info(
    app: str = "",
    package: str = "",
    serial: str = "",
    *,
    choose: _common.ChooseCb = None,
    **_elicitors,
) -> str:
    """``qa_app_info``: the installed version of one app on one device.

    The device and the app are resolved or asked, never guessed; a read
    touches nothing on the device. Never raises."""
    if not _common._mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        from tools.mobile import app_pick, app_tools, device_pick
        from tools.mobile.resolve import RESOLVED

        ask = mobile_run._mobile_ask(choose)
        device = await device_pick.pick_device(str(serial or "").strip(), ask=ask)
        if device.status != RESOLVED:
            return mobile_step._mobile_unresolved("device", device)
        picked = await app_pick.pick_package(
            app_pick.PickRequest(str(app or ""), device.value, str(package or "")),
            ask=ask,
        )
        if picked.status != RESOLVED:
            return mobile_step._mobile_unresolved("app", picked)
        return await app_tools.app_info_text(device.value, picked.value)
    except mobile_step._MOBILE_TOOL_FAILURES as exc:
        return mobile_step._mobile_tool_failed(exc, "qa_app_info")


async def handle_update_app(
    app: str = "",
    package: str = "",
    serial: str = "",
    source: str = "",
    *,
    choose: _common.ChooseCb = None,
    **_elicitors,
) -> str:
    """``qa_update_app``: update one app to the source's latest build through
    ``app_tools.run_update``.

    ``update_app`` resolves or asks the device and the app; a version it
    cannot install over is offered as an uninstall the tester must confirm.
    With no target version the verify needs a code above the previous one.
    Never raises."""
    if not _common._mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        from tools.mobile import app_tools, update_app

        request = update_app.UpdateRequest(
            serial=str(serial or "").strip(),
            app_text=str(app or ""),
            package=str(package or "").strip(),
            source=str(source or "").strip() or update_app.SOURCE_DEFAULT,
        )
        return await app_tools.run_update(request, ask=mobile_run._mobile_ask(choose))
    except mobile_step._MOBILE_TOOL_FAILURES as exc:
        return mobile_step._mobile_tool_failed(exc, "qa_update_app")


async def handle_setup_capture(
    serial: str = "",
    action: str = "prepare",
    apply: bool = False,
    capture_ack: bool = False,
) -> str:
    """``qa_setup_capture``: prepare / check / remove a device's API-capture
    trust AHEAD of a run. Never raises.

    ``prepare`` calls the SAME ``tools.mobile_capture.prepare`` the run's own
    consent stage calls (T3.2a/T4.2a) -- this tool never re-decides
    anything, and a second, unrelated caller on the same serial is refused
    by name rather than racing the run. ``remove`` needs ``apply=true`` like
    every other device-touching step in this lane (brief decision 8: no new
    flag -- the certificate install reads the lane's kill-switch itself, in
    ``cert.install``, and ``apply`` is the per-call gate).
    """
    if not _common._mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()

    from tools.mobile import render as mobile_render
    from tools.untrusted import single_line as _safe

    serial = str(serial or "").strip()
    if not serial:
        return (
            "\u26a0\ufe0f `serial` is required -- pass the device's adb serial. "
            "Nothing was touched."
        )
    chosen = str(action or "prepare").strip().lower()
    if chosen not in ("prepare", "status", "remove"):
        return (
            "\u26a0\ufe0f Unknown action "
            + repr(_safe(chosen, 40))
            + " -- `action` must be one of `prepare`, `status`, `remove`. "
            "Nothing was touched."
        )

    if chosen == "status":
        return _setup_capture_status(serial)
    if chosen == "remove":
        return await _setup_capture_remove(serial, apply)
    # action == "prepare"
    if not (apply and capture_ack):
        return mobile_render.capture_menu_markdown()
    return await _setup_capture_prepare(serial, apply)


def _setup_capture_status(serial: str) -> str:
    """The ``status`` action's markdown for *serial*."""
    from tools.mobile_capture import ca, cert, ledger
    from tools.untrusted import single_line as _safe

    made = ca.ensure_ca()
    fingerprint = (
        (made.get("content") or {}).get("fingerprint") if not made.get("error") else ""
    )
    known = ledger.tier_for(serial, fingerprint or "")
    body = (known.get("content") or {}) if not known.get("error") else {}
    cert_status = cert.status(serial).get("content") or {}
    cert_line = (
        "yes, via the " + _safe(str(cert_status.get("route") or ""), 20) + " store"
        if cert_status.get("installed")
        else "no"
    )
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


async def _setup_capture_remove(serial: str, apply: bool) -> str:
    """The ``remove`` action: refused without ``apply``, else clear and remove."""
    from tools.untrusted import single_line as _safe

    if not apply:
        return (
            "\u26a0\ufe0f Removing the certificate touches the device and "
            "needs `apply=true`; nothing was removed."
        )
    from tools.mobile_capture import cert, teardown

    cleared = await teardown.clear(serial, reason="qa_setup_capture_remove")
    removed = await cert.remove(serial, apply=True)
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


async def _setup_capture_prepare(serial: str, apply: bool) -> str:
    """The ``prepare`` action's markdown, once consent is given."""
    from tools import mobile_capture as api_capture
    from tools.untrusted import single_line as _safe

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


async def handle_mobile_status(
    run_id: str = "",
    session_token: str = "",
    report_now: bool = False,
    *,
    progress: _common.ProgressCb = None,
) -> str:
    """Where everything stands, read from disk only. Never drives the device.

    Deliberately the one tool in this lane that never touches the emulator: it
    is what a tester calls after an install or a boot, both of which outlive a
    tool call by design.

    ``report_now=True`` is the one thing here that WRITES: it renders the run's
    standalone HTML report -- mid-run is fine and is labelled partial -- into
    that run's own directory and names the path. It does NOT open it, and it
    still touches no device. Default False, so a plain status poll changes
    nothing on disk.
    """
    # tools.untrusted, not agents.api_test_agent -- see handle_mobile_test.
    from tools.untrusted import single_line as _safe

    if not _common._mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()

    from tools.mobile.session_core import STATE_ABANDONED
    from tools.mobile.session_runs import resolve, summary

    try:
        if not run_id:
            return _mobile_status_listing()
        resolved = resolve(run_id, session_token)
        if resolved.get("error"):
            return "⚠️ " + _safe(resolved["error"], 400)
        body = resolved["content"] or {}
        recorded = summary(run_id).get("content") or []
        abandoned = str(body.get("state") or "") == STATE_ABANDONED
        # D7 (2026-09-03): an abandoned run with work on disk gets its report
        # WITHOUT being asked; see `_mobile_status_report`.
        report = ("", "")
        if report_now or (abandoned and recorded):
            report = await _mobile_status_report(run_id, body)
        return _mobile_status_text(run_id, body, recorded, report)
    except Exception as exc:  # never-raise contract
        logger.exception("mcp mobile_status failed")
        _common._capture_error(exc, "qa_mobile_status")
        return "⚠️ The mobile status could not be read: " + _safe(str(exc), 200)


def _mobile_status_text(
    run_id: str, body: dict, recorded: list, report: tuple[str, str]
) -> str:
    """Compose the status reply; *report* is ``(path, error)`` from the render."""
    from tools.mobile import render as mobile_render
    from tools.mobile.session_core import STATE_ABANDONED
    from tools.mobile.session_records import device_line, reset_step_status, run_verdict
    from tools.mobile.session_runs import coverage_line

    # ONE producer for "how far did this run get", handed to BOTH blocks of
    # this reply. They used to derive it separately -- one from verdicts,
    # one from statuses -- and contradict each other three lines apart.
    coverage = coverage_line(body, recorded)
    abandoned = str(body.get("state") or "") == STATE_ABANDONED
    relay = ""
    if body.get("finished") or abandoned:
        relay = mobile_render.relay_block(
            verdict=run_verdict(body, recorded),
            reset=reset_step_status(body, recorded),
            typed_field_line=mobile_render.typed_field_tally(recorded),
        )
    return (
        relay
        + mobile_render.status_block(body, coverage)
        # WHICH HARDWARE. A pass on an emulator is weaker evidence than a
        # pass on the phone, so the run says which it ran on -- from the
        # record written when it was planned, not from a fresh probe, so a
        # finished run still answers and two readers cannot disagree.
        + device_line(body)
        + "\n\n"
        + mobile_render.summary_block(
            recorded,
            coverage_line=coverage,
            run_id=run_id,
            partial=not body.get("finished"),
            abandoned=abandoned,
            report_path=report[0],
            report_error=report[1],
        )
    )


def _mobile_status_listing() -> str:
    """The status reply when no run id was given: the newest runs on disk."""
    from tools.mobile.session_runs import list_runs

    runs = (list_runs(10) or {}).get("content") or []
    if not runs:
        return "## No mobile runs yet\n\nCall `qa_mobile_test` to start one."
    listed = "\n".join(_mobile_run_row(row) for row in runs)
    return "## Mobile runs on this machine\n\n" + listed


async def _mobile_status_report(run_id: str, body: dict) -> tuple[str, str]:
    """Render the run's report; return ``(path, error)``, one of them empty.

    Rendering is bounded to a run that recorded something (or an explicit
    ``report_now``), so an abandoned run that never got past its first case
    does not write an empty page.
    """
    from tools.mobile import report as mobile_report
    from tools.mobile.session_packets import finish_evidence

    # App evidence (plan P2/D5): the app's own event log is pulled ONCE,
    # wherever the run reaches its report. The submit path does this in
    # `session`; a report asked for HERE -- a run finished in another
    # chat, or abandoned -- would otherwise render `events_source: none`
    # for events nobody asked the device for. Idempotent on the
    # manifest, never raises, never changes the state or a verdict; this
    # is the one device touch in this tool and it happens only on the
    # same condition that writes the report.
    await finish_evidence(run_id, body)
    produced = mobile_report.render(run_id)
    if produced.get("error"):
        return "", str(produced["error"])
    return str((produced.get("content") or {}).get("path") or ""), ""


def _mobile_run_row(row: object) -> str:
    """One line of the run listing: what it is, where it stands, what it said.

    E5 (2026-09-04): the listing gave the lane and a case count, so a tester
    looking for the run they had abandoned could not tell it from the seven
    others -- and neither could the model, which started an eighth. The state
    and the newest verdict are what distinguish them.

    Never raises and never lets a bad run break the listing: a row that cannot
    be resolved falls back to the plain line it always had.
    """
    from tools.mobile.session_runs import resolve, summary

    body = row if isinstance(row, dict) else {}
    manifest = body.get("manifest") or {}
    run_id = str(body.get("run_id") or "?")
    line = (
        "- `"
        + run_id
        + "` — "
        + str(manifest.get("lane") or "?")
        + " lane, "
        + str(manifest.get("total") or 0)
        + " case(s)"
    )
    try:
        resolved = (resolve(run_id) or {}).get("content") or {}
        if resolved:
            line += ", state **" + str(resolved.get("state") or "unknown") + "**"
        recorded = [
            case
            for case in ((summary(run_id) or {}).get("content") or [])
            if isinstance(case, dict)
        ]
        verdicts = [str(case.get("verdict") or "") for case in recorded]
        verdicts = [verdict for verdict in verdicts if verdict]
        if verdicts:
            line += ", last verdict " + verdicts[-1]
        elif recorded:
            line += ", no verdict yet"
    except Exception:  # pragma: no cover - a listing must never raise
        logger.info("mcp mobile: could not enrich the listing row for %s", run_id)
    return line


async def handle_list_devices(*, progress: _common.ProgressCb = None) -> str:
    try:
        await _common._emit(progress, "📱 Discovering connected devices…")
        # The tester asked for a scan: never answer it from the TTL cache.
        result = await list_devices(refresh=True)
        if result.get("error"):
            return f"⚠️ Device discovery failed: {result['error']}"
        devices = await _reply._android_device_details(result.get("content") or [])
        unusable = result.get("unusable") or []
        await _common._audit(
            "mcp_list_devices",
            detail={"count": len(devices), "unusable": len(unusable)},
        )
        # Early in a boot adb does not list the emulator at all, so the only
        # evidence is that this server started it (fix round 3, item 5).
        booting: list = []
        if not devices and not unusable:
            try:
                from tools.mobile import emulator as mobile_emulator

                booting = mobile_emulator.recently_started()
            except Exception:
                logger.debug("booting probe failed", exc_info=True)
        # B3: an empty list is explained (AVDs on file + a bounded acceleration
        # check), never by the minute-long WHPX feature query.
        empty_hint = ""
        if not devices and not unusable and not booting:
            try:
                from tools.mobile import emulator as mobile_emulator

                empty_hint = mobile_emulator.render_no_devices_hint(
                    await mobile_emulator.diagnose_no_devices()
                )
            except Exception:
                logger.debug("no-devices diagnosis failed", exc_info=True)
        return _reply.shape_devices(
            devices,
            unusable,
            result.get("adb_dropped_lines") or 0,
            booting=booting,
            empty_hint=empty_hint,
            adb_problem=str(result.get("adb_problem") or ""),
        )
    except Exception as exc:
        logger.exception("handle_list_devices failed")
        _common._capture_error(exc, "qa_list_devices")
        return f"⚠️ Device discovery failed: {exc}"


async def handle_network_watch(
    action: str = "status", serial: str = "", package: str = "", apply: bool = False
) -> str:
    """Passive, root-free network watch for a GUI client. Never raises.

    Thin by construction: `tools/mobile_evidence/network_watch.py` only gives its own package's
    EXISTING console capture a run-free entry point. Nothing about how traffic
    is observed, parsed or redacted is decided here or in the desktop app.
    """
    await _common._audit("mcp_network_watch")
    try:
        from tools.mobile_evidence import network_watch

        payload = await network_watch.handle(action, serial, package, bool(apply))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("handle_network_watch failed")
        payload = {"error": str(exc), "content": None}
    return "```json\n" + json.dumps(payload, indent=2, default=str) + "\n```"
