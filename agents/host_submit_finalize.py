"""Finalize stage of qa_submit_suite (moved from handlers, C3a step 3a).

ONE synthetic CategoryResult so finalize_generation's "N of 8 failed"
partial line correctly does NOT fire (its only use of category_results).
The exporter reads the suite's assumed-artifacts PrivateAttr to add the
sheet; absent means no sheet, so a run with the review off is byte-identical.

Honesty rule: a suppression the tester cannot see IS a silent downgrade. The
Feature Analysis skip note is emitted only when the standalone tool is
enabled (so the tester expects a report) but the inline one is off on this
path. Attached to the FINALIZE reply only, not the intermediate gap reply.
The edition gate (test-cases-only) is the only thing deciding that now.

Batch 1, M1: the volume gate measured the SUBMITTED cases on purpose, so a
host is never refused for volume the server itself removed as duplicates.
That leaves one gap: padding with near-duplicates. The FINAL suite is
re-measured and, only where the verdict would have been a refusal, a WARNING
is added -- never a refusal, because by then the suite is finalized.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from agents.host_prep_volume import VolumeQuery
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubFinal, SubRun
from agents.test_scenario_agent import CategoryResult
from config.settings_guard import never_raise
from tools import prep_store

logger = logging.getLogger("agents.host_mode")

_FA_SKIP = (
    "> ℹ️  Feature Analysis report SKIPPED for this "
    "host-mode submit. Call `qa_feature_analysis` on demand if "
    "you want it -- it is chat-only, like everything else here.\n\n"
)


class FinIn(NamedTuple):
    prepared: Any
    all_cases: list
    assumed_rows: list
    progress: Any


class VolumeIn(NamedTuple):
    meta: dict
    suite: Any
    prep_id: str
    volume_note: str


def fa_skip_note(ports: SubmitPorts) -> str:
    fa_skip_note = ""
    if not ports.common.test_cases_only():
        fa_skip_note = _FA_SKIP
    return fa_skip_note


def _attach_assumed(suite: Any, assumed_rows: list) -> None:
    with never_raise(
        logger,
        "Could not attach the Assumed Requirements rows",
        level=logging.WARNING,
        exc_info=True,
    ):
        suite._assumed_artifacts = {"rows": assumed_rows}


def _make_hooks(args: FinIn, ports: SubmitPorts) -> tuple[dict, Any, Any]:
    captured: dict = {}

    def _on_ready(s) -> None:
        captured["suite"] = s
        if args.assumed_rows:
            _attach_assumed(s, args.assumed_rows)

    async def _on_status(msg: str) -> None:
        await ports.common.emit(args.progress, msg)

    return captured, _on_ready, _on_status


async def run_finalize(args: FinIn, ports: SubmitPorts) -> SubFinal:
    category_results = [
        CategoryResult(
            category_name="Host Submission", cases=args.all_cases, error=None
        )
    ]
    captured, on_ready, on_status = _make_hooks(args, ports)
    skip_note = fa_skip_note(ports)
    summary, _x, _c, _t, status = await ports.io.finalize_generation(
        args.prepared,
        args.all_cases,
        category_results,
        defer_files=True,
        on_suite_ready=on_ready,
        on_status=on_status,
        ui_content=args.prepared.ui_content,
    )
    return SubFinal(captured.get("suite"), summary, status, skip_note)


async def finalize_submit(
    run: SubRun, progress: Any, ports: SubmitPorts
) -> str | SubFinal:
    prep_id = run.prep.prep_id
    dropped_note = run.gates.dropped_note
    conflict_note = run.read.conflict_note
    fin_in = FinIn(
        run.prep.prepared, run.dup.all_cases, run.ground.assumed_rows, progress
    )
    fin = await run_finalize(fin_in, ports)
    suite = fin.suite
    if suite is None or not getattr(suite, "test_cases", None):
        await prep_store.delete_prep(prep_id)
        return (
            f"{dropped_note}{conflict_note}⚠️ The submitted suite "
            "produced no usable test cases after validation. Regenerate and "
            "resubmit."
        )
    return fin


def post_dedup_volume_note(args: VolumeIn, ports: SubmitPorts) -> str:
    volume_note = args.volume_note
    if not volume_note:
        query = VolumeQuery(
            args.meta,
            list(getattr(args.suite, "test_cases", None) or []),
            args.prep_id,
            post_dedup=True,
        )
        _pmode, _pmd = ports.prep_gate.volume_floor_note(query)
        if _pmode:
            volume_note = _pmd
    return volume_note
