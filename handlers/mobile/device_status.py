"""``qa_mobile_status`` for ``mobile_device``: where a run stands, from disk."""

from __future__ import annotations

import logging

from handlers import _common, mobile_gate
from handlers.mobile.device_requests import StatusArgs, StatusView
from handlers.mobile.device_runs import _mobile_status_listing
from tools.mobile import (
    knowledge_close,
    run_finalize_done,
    session_packets,
    session_records,
    session_runs,
)
from tools.mobile import render as mobile_render
from tools.mobile import report as mobile_report
from tools.mobile.replay import stop_shot
from tools.mobile.session_core import STATE_ABANDONED
from tools.untrusted import single_line as _safe

logger = logging.getLogger("tools.mcp_handlers")


def _abandoned(body: dict) -> bool:
    """Whether the run in *body* was abandoned."""

    return str(body.get("state") or "") == STATE_ABANDONED


# App evidence (plan P2/D5): the app's own event log is pulled ONCE,
# wherever the run reaches its report. The submit path does this in
# `session`; a report asked for HERE -- a run finished in another
# chat, or abandoned -- would otherwise render `events_source: none`
# for events nobody asked the device for. Idempotent on the
# manifest, never raises, never changes the state or a verdict; this
# is the one device touch in this tool and it happens only on the
# same condition that writes the report.
async def _pull_evidence(run_id: str, body: dict) -> None:
    """Pull the app's event log for the run once, before its report."""

    await session_packets.finish_evidence(run_id, body)


async def _mobile_status_report(run_id: str, body: dict) -> tuple[str, str]:
    """Render the run's report; return ``(path, error)``, one of them empty.

    Rendering is bounded to a run that recorded something (or an explicit
    ``report_now``), so an abandoned run that never got past its first case
    does not write an empty page.
    """

    await _pull_evidence(run_id, body)
    await stop_shot.drain_all()  # B11: the report reads these PNGs
    produced = mobile_report.render(run_id)
    if produced.get("error"):
        return "", str(produced["error"])
    return str((produced.get("content") or {}).get("path") or ""), ""


def _status_relay(body: dict, recorded: list) -> str:
    """The relay block of a finished or abandoned run, else ``""``."""

    if not (body.get("finished") or _abandoned(body)):
        return ""
    word = session_records.run_verdict(body, recorded)
    return mobile_render.relay_block(
        verdict=run_finalize_done.qualified_verdict(word, recorded),
        reset=session_records.reset_step_status(body, recorded),
        typed_field_line=mobile_render.typed_field_tally(recorded),
    )


def _summary_text(view: StatusView, coverage: str) -> str:
    """The per-case summary block of the status reply."""

    opts = mobile_render.SummaryOpts(
        coverage_line=coverage,
        run_id=view.run_id,
        partial=not view.body.get("finished"),
        abandoned=_abandoned(view.body),
        report_path=view.report[0],
        report_error=view.report[1],
    )
    return mobile_render.summary_block(view.recorded, opts)


# Compose the status reply; *report* is ``(path, error)`` from the render (now `view.report`).
def _mobile_status_text(view: StatusView) -> str:
    """Compose the status reply; *report* is ``(path, error)`` from the render."""

    # ONE producer for "how far did this run get", handed to BOTH blocks of
    # this reply. They used to derive it separately -- one from verdicts,
    # one from statuses -- and contradict each other three lines apart.
    coverage = session_runs.coverage_line(view.body, view.recorded)
    relay = _status_relay(view.body, view.recorded)
    # WHICH HARDWARE. A pass on an emulator is weaker evidence than a
    # pass on the phone, so the run says which it ran on -- from the
    # record written when it was planned, not from a fresh probe, so a
    # finished run still answers and two readers cannot disagree.
    hardware = session_records.device_line(view.body)
    summary = _summary_text(view, coverage)
    head = relay + mobile_render.status_block(view.body, coverage)
    learn = knowledge_close.learn_line(view.run_id)
    learn = "\n\nKnowledge: " + learn if learn else ""
    return head + hardware + learn + "\n\n" + summary


async def _status_reply(request: StatusArgs) -> str:
    """The status reply for a request that names a run."""

    resolved = session_runs.resolve(request.run_id, request.session_token)
    if resolved.get("error"):
        return "⚠️ " + _safe(resolved["error"], 400)
    body = resolved["content"] or {}
    recorded = session_runs.summary(request.run_id).get("content") or []
    # D7 (2026-09-03): an abandoned run with work on disk gets its report
    # WITHOUT being asked; see `_mobile_status_report`.
    report = ("", "")
    if request.report_now or (_abandoned(body) and recorded):
        report = await _mobile_status_report(request.run_id, body)
    return _mobile_status_text(StatusView(request.run_id, body, recorded, report))


# Deliberately the one tool in this lane that never touches the emulator: it
# is what a tester calls after an install or a boot, both of which outlive a
# tool call by design.
#
# ``report_now=True`` is the one thing here that WRITES: it renders the run's
# standalone HTML report -- mid-run is fine and is labelled partial -- into
# that run's own directory and names the path. It does NOT open it, and it
# still touches no device. Default False, so a plain status poll changes
# nothing on disk.
async def handle_mobile_status(request: StatusArgs | None = None) -> str:
    """Where everything stands, read from disk only. Never drives the device."""
    # tools.untrusted, not agents.api_test_agent -- see handle_mobile_test.

    if not _common.mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    request = request or StatusArgs()
    try:
        if not request.run_id:
            return _mobile_status_listing()
        return await _status_reply(request)
    except Exception as exc:  # never-raise contract
        logger.exception("mcp mobile_status failed")
        _common.capture_error(exc, "qa_mobile_status")
        return "⚠️ The mobile status could not be read: " + _safe(str(exc), 200)
