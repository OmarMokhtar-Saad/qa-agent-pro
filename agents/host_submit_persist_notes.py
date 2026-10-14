"""Generation-notes attachment for qa_submit_suite (moved from handlers, C3a step 3b).

F5/F6/F7 (2026-08-15): carry this run's SHORTFALLS into the workbook. Each was
already disclosed in the reply, but the reply is transient chat that a
summarising host model prunes, while the .xlsx is what the tester keeps.
Attached after finalize and before the export, because only then are all
verdicts known and the suite is still the object about to be written.
Best-effort: a suite with nothing to report gets no sheet, and a failure here
must never cost the tester the export.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from agents.host_category_calls import EarlyNotesIn
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubFinal, SubRun, SubSaved
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


class NotesIn(NamedTuple):
    run: SubRun
    fin: SubFinal
    saved: SubSaved


def _sync_notes(args: NotesIn, ports: SubmitPorts) -> list:
    run, cat = args.run, ports.category
    notes: list = []
    notes += cat.checklist_gap_gen_notes(run.checklist.gap)
    notes += cat.ambiguity_gen_notes(run.amb.result)
    notes += cat.step_assertion_gen_notes(
        run.gates.assert_findings, run.gates.data_findings
    )
    notes += cat.quality_advisory_gen_notes(
        run.dup.all_cases, run.prep.source_text, args.fin.summary
    )
    return notes


async def attach_generation_notes(args: NotesIn, ports: SubmitPorts) -> None:
    run, suite, prov = args.run, args.fin.suite, args.saved.prov
    # pragma: no cover - defensive; must never block export
    with never_raise(logger, "generation notes attachment failed", exc_info=True):
        _gen_notes = await ports.category.early_gen_notes(
            EarlyNotesIn(
                run.prep.meta,
                prov.dup_case_note,
                run.prep.source_text,
                prov.provenance,
                suite,
            )
        )
        _gen_notes += _sync_notes(args, ports)
        if _gen_notes:
            suite._generation_notes = _gen_notes
