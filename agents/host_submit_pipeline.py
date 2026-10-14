"""qa_submit_suite orchestrator (C3a step 4).

load -> read/gates/reviews -> finalize/quality -> persist/audit -> export/reply,
each stage a sibling module. Every collaborator the handler owns arrives through
``ports`` (built once per call by the handler, read at call time), so test patches
on ``handlers.submit_suite`` stay effective. A ``str`` from any stage is the
finished reply (a refusal) and ends the run.
"""

from __future__ import annotations

from typing import NamedTuple

from agents import (
    host_submit_audit,
    host_submit_dup_notes,
    host_submit_export,
    host_submit_load,
    host_submit_persist,
    host_submit_persist_notes,
    host_submit_reply,
)
from agents.host_submit_pipeline_post import finish
from agents.host_submit_pipeline_run import SubmitCall, build_run
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubPost, SubRun, SubSaved


class Staged(NamedTuple):
    """A finished, quality-gated submission ready to persist and reply."""

    call: SubmitCall
    run: SubRun
    post: SubPost


async def persist(st: Staged, ports: SubmitPorts) -> SubSaved:
    run, fin = st.run, st.post.fin
    save_in = host_submit_persist.SaveIn(
        run.prep, run.read.rows, fin.suite, st.call.cbs[1]
    )
    saved = await host_submit_persist.save_submitted_suite(save_in, ports)
    notes_in = host_submit_persist_notes.NotesIn(run, fin, saved)
    await host_submit_persist_notes.attach_generation_notes(notes_in, ports)
    art_in = host_submit_persist.ArtifactsIn(run.prep, saved, fin.suite)
    await host_submit_persist.persist_artifacts(art_in, ports)
    rec_in = host_submit_audit.RecordIn(run, fin, saved)
    await host_submit_audit.record_submit(rec_in, ports)
    return saved


def build_tail(st: Staged, ports: SubmitPorts) -> host_submit_reply.SubTail:
    # Batch C items 1 + 4: two NOTES, never refusals, computed once HERE so
    # both finalize routes (Path A rows, Path B merged suite_json) are covered.
    # Not on the fix-and-resubmit early returns: the suite is not final there.
    # No coverage view is built, so the coverage-signal note always fires.
    suite = st.post.fin.suite
    pre_in = host_submit_dup_notes.PrescreenIn(suite, st.run.read.parsed, st.run.dup)
    return host_submit_reply.SubTail(
        *host_submit_dup_notes.prescreen_notes(pre_in, ports),
        ports.prep_gate.rtm_orphan_note(suite),
        ports.prep_gate.no_coverage_signal_note(None),
    )


async def publish(st: Staged, ports: SubmitPorts) -> str:
    """Save, persist and export the finished suite, then build the reply."""
    run, suite = st.run, st.post.fin.suite
    saved = await persist(st, ports)
    ask_text, progress = st.call.cbs
    exp_in = host_submit_export.ExportIn(run.prep, suite, ask_text, progress)
    export = await host_submit_export.export_submitted_suite(exp_in, ports)
    tail = build_tail(st, ports)
    await host_submit_export.stamp_prep_finalized(run.prep, suite, export.paths)
    reply_in = host_submit_reply.ReplyIn(run, st.post, saved, export, tail)
    return host_submit_reply.submit_reply(reply_in, ports)


async def run_submit_suite(call: SubmitCall, ports: SubmitPorts) -> str:
    """Run one qa_submit_suite call end to end and return the reply text."""
    prep = await host_submit_load.load_prep(call.prep_id, ports)
    if isinstance(prep, str):
        return prep
    run = await build_run(call, prep, ports)
    if isinstance(run, str):
        return run
    post = await finish(call, run, ports)
    if isinstance(post, str):
        return post
    return await publish(Staged(call, run, post), ports)
