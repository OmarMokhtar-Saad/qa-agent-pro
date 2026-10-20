"""Mobile session: the report-time event-log pull. Cut verbatim from tools/mobile/session_packets.py (C5 w17)."""

from __future__ import annotations

import functools
import logging

from tools.mobile import evidence_package, explore_stop, run_store
from tools.mobile.run_finalize import finalize_explore_cases
from tools.mobile_evidence import capture

logger = logging.getLogger("tools.mobile.session")


class _NotClosed(Exception):
    """A close that failed; lru_cache stores no exception, so it is retried."""


@functools.lru_cache(maxsize=256)
def _close_once(run_id: str) -> None:
    if finalize_explore_cases(run_id) is False:
        raise _NotClosed(run_id)


def finalise_once(run_id: str) -> None:
    """Close the run's explore cases on the first good call per run, not on every poll.

    The bounded cache is the only memory: no module-level dict, so a restart
    simply closes them once more (finalising is idempotent). A failed close
    is not cached, so the next poll tries again."""
    try:
        _close_once(run_id)
    except _NotClosed:
        logger.warning("mobile.session: explore cases not closed for %s", run_id)


def _merge_events(run_id: str, manifest: dict, pulled: object) -> None:
    content = pulled.get("content") if isinstance(pulled, dict) else None
    content = content if isinstance(content, dict) else {}
    # RE-READ after the await: a state or lease write may have landed while
    # the device was being read, and the copy taken before the pull would
    # roll it back. Only the three evidence keys are merged into the fresh copy.
    fresh = (run_store.read_manifest(run_id) or {}).get("content")
    fresh = fresh if isinstance(fresh, dict) else manifest
    fresh["events_source"] = str(content.get("events_source") or "none")
    fresh["events_reason"] = str(
        content.get("reason")
        or (pulled.get("error") if isinstance(pulled, dict) else "")
        or ""
    )[:400]
    fresh["events_segments"] = content.get("segments")
    run_store.write_manifest(run_id, fresh)


# Pull the app's own event log ONCE, when a run reaches its report.
#
# Idempotent on the manifest: a run whose manifest already records an
# ``events_source`` is not pulled again, so a resumed chat that lands on the
# finished state a second time makes no adb call. ``capture.pull_events``
# itself skips, with a reason, when the package has no profile, the flag is
# off, or ``run-as`` is refused (a release build) -- and whatever it says is
# recorded so the report can show it (plan D8). Nothing here can change the
# run's state or a verdict: every exception ends in this function.
async def _finish_evidence(run_id: str, resolved: object) -> None:
    try:
        body = resolved if isinstance(resolved, dict) else {}
        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        if isinstance(manifest, dict) and explore_stop.display_stop(manifest):
            # Every end path (deadline, budget, report-now) passes here.
            finalise_once(run_id)
        if not isinstance(manifest, dict) or manifest.get("events_source"):
            return
        manifest = await evidence_package.refile(run_id, manifest)
        pulled = await capture.pull_events(
            capture.RunRef(
                str(body.get("serial") or manifest.get("serial") or ""),
                str(manifest.get("package") or body.get("package") or ""),
                run_id,
            )
        )
        _merge_events(run_id, manifest, pulled)
    except Exception:  # never-raise: evidence is not a verdict
        logger.exception("mobile.session._finish_evidence failed")


# The public face of :func:`_finish_evidence`, for callers outside this module.
#
# ``qa_mobile_status(report_now=True)`` renders a run's report from another chat
# and must pull the app's event log first, once; it goes through here so that a
# module boundary is crossed by a public name, not a private one. Same contract:
# idempotent on the manifest, never raises, never changes state or a verdict.
async def finish_evidence(run_id: str, resolved: object) -> None:
    await _finish_evidence(run_id, resolved)
