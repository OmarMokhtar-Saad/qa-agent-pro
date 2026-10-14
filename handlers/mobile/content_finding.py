"""Folding a tester ``finding`` into a mobile step script."""

from __future__ import annotations

import asyncio
import json

from handlers.mobile.content_args import StepCall, _step_arg, _with_script
from tools.mobile import actions, run_store, step_finding
from tools.mobile.session_core import LANE_SUITE


def _finding_inputs(call: StepCall) -> tuple:
    """``(manifest, script, payload)`` of the step call's run and script."""

    manifest = run_store.read_manifest(_step_arg(call, "run_id")).get("content")
    script = _step_arg(call, "script")
    return manifest, script, actions.decode_reply(script)


def _merge(payload: dict, finding: object, manifest: dict) -> tuple:
    """``(merged, refusal)`` from ``step_finding.merge_finding`` on the run's lane."""

    lane = str(manifest.get("lane") or LANE_SUITE)
    return step_finding.merge_finding(payload, finding, lane=lane)


def _mobile_merge_finding(args: tuple, kwargs: dict, finding: object) -> tuple:
    """``(args, kwargs, note)`` with ``finding`` folded into the step script.

    Explore lane only (``step_finding.merge_finding``); on any other lane the
    script is untouched and the note says the finding was left out. An
    unreadable run or script is left alone: the step itself reports that."""
    manifest, script, payload = _finding_inputs(StepCall(args, kwargs))
    if not isinstance(manifest, dict) or (script.strip() and not payload):
        return args, kwargs, ""
    merged, refusal = _merge(payload, finding, manifest)
    if refusal:
        return args, kwargs, refusal + "\n\n"
    if not script.strip():
        merged["actions"] = []
    args, kwargs = _with_script(args, kwargs, json.dumps(merged))
    return args, kwargs, ""


async def _mobile_fold_finding(args: tuple, kwargs: dict, finding: str) -> tuple:
    """``_mobile_merge_finding`` off the event loop; no finding, no change."""
    if not finding:
        return args, kwargs, ""
    return await asyncio.to_thread(_mobile_merge_finding, args, kwargs, finding)
