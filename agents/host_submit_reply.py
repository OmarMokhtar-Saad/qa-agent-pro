"""Reply sections of qa_submit_suite (moved from handlers, C3a step 3c).

Slot ORDER is the contract: the ambiguity verdict LEADS (FIX 2, 2026-08-09),
then volume floor, step assertions, case quality and a MISSING checklist
(F5/F6, 2026-08-15) ahead of the Excel export, because a host summarising the
reply keeps the .xlsx path and drops what follows it. A checklist note that is
NOT a gap stays in the tail with the informational sections.

A ``protected`` section is never dropped or truncated. D4 (2026-08-21): the
duplicate-prescreen HEAD is protected (it states the duplicate-review assurance
above it is contradicted, bounded at 496 chars); the PAIRS are trimmable at
P_EXPORTED because every pair is printed in the workbook. assemble_finalize_reply
sorts (priority, INDEX), so the pairs yield after the checklist NLI note.
tests/test_dup_prescreen_merged_submit.py pins the split.

Two shaping passes, both pure: the first measures the header the summary sits
under, so the budget is exact rather than a reserved guess.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from agents.host_submit_export import SubExport
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubPost, SubRun, SubSaved


class SubTail(NamedTuple):
    prescreen_head: str
    prescreen_pairs: str
    rtm_note: str
    cov_signal_note: str


class ReplyIn(NamedTuple):
    run: SubRun
    post: SubPost
    saved: SubSaved
    export: SubExport
    tail: SubTail


def _lead_sections(args: ReplyIn, ports: SubmitPorts) -> list:
    run, post, saved = args.run, args.post, args.saved
    sec = ports.reply.ReplySection
    note = getattr(args.post.fin.suite, "_family_note", None) or ""
    return [
        sec("earlier versions", "> " + note if note else "", protected=True),
        sec("ambiguity screening", run.amb.note, protected=True),
        sec("session provenance", saved.prov.session_note, protected=True),
        sec("volume floor", post.volume_note, protected=True),
        sec("step assertions", run.gates.assertion_note, protected=True),
        sec("case quality", post.quality_note, protected=True),
    ]


def _gap_sections(args: ReplyIn, ports: SubmitPorts) -> list:
    run, saved = args.run, args.saved
    note, gap = run.checklist.note, run.checklist.gap
    sec = ports.reply.ReplySection
    return [
        sec("checklist gap", note if gap else "", protected=True),
        sec("suite persistence", saved.persist_note, protected=True),
        sec("Excel export", args.export.note, protected=True),
        sec("server version", run.prep.version_note, protected=True),
        sec("dropped cases", run.gates.dropped_note, protected=True),
    ]


def _provenance_sections(args: ReplyIn, ports: SubmitPorts) -> list:
    run, post = args.run, args.post
    note, gap = run.checklist.note, run.checklist.gap
    sec, rep = ports.reply.ReplySection, ports.reply
    return [
        sec("unused staged rows", run.read.conflict_note, protected=True),
        sec("category provenance", run.cases.cat_source, rep.P_PROVENANCE),
        sec("Feature Analysis pointer", post.fin.fa_skip_note, rep.P_POINTER),
        sec("acceptance criteria", run.ac.note, protected=True),
        sec("grounding", run.ground.note, protected=True),
        sec("checklist coverage", "" if gap else note, rep.P_REPORT),
    ]


def _dup_sections(args: ReplyIn, ports: SubmitPorts) -> list:
    run, post, saved = args.run, args.post, args.saved
    sec = ports.reply.ReplySection
    return [
        sec("screenshots", run.images.note, protected=True),
        sec("duplicate review", run.dup.status_note, protected=True),
        sec("duplicates", post.dup_note, protected=True),
        sec("duplicate cases", saved.prov.dup_case_note, protected=True),
    ]


def _tail_sections(args: ReplyIn, ports: SubmitPorts) -> list:
    tail = args.tail
    sec = ports.reply.ReplySection
    return [
        sec("duplicate prescreen", tail.prescreen_head, protected=True),
        # K-12: the pairs are the evidence the host must act on (K-09),
        # bounded by _DUP_SHORTLIST_MAX_PAIRS, so they are never dropped.
        sec("duplicate prescreen pairs", tail.prescreen_pairs, protected=True),
        sec("traceability orphans", tail.rtm_note, protected=True),
        sec("coverage signal", tail.cov_signal_note, protected=True),
    ]


def reply_sections(args: ReplyIn, ports: SubmitPorts) -> list:
    return [
        *_lead_sections(args, ports),
        *_gap_sections(args, ports),
        *_provenance_sections(args, ports),
        *_dup_sections(args, ports),
        *_tail_sections(args, ports),
    ]


def _generation_md(args: ReplyIn, rep: Any, summary: str, **extra: Any) -> str:
    """The generation-result markdown for *summary* (``summary_cap`` in *extra*)."""
    fin = args.post.fin
    return rep.shape_generation_result(
        rep.GenerationReply(
            summary,
            fin.suite,
            args.saved.suite_id,
            fin.status,
            auto_export=args.export.auto_export,
            **extra,
        )
    )


def submit_reply(args: ReplyIn, ports: SubmitPorts) -> str:
    rep = ports.reply
    sections = reply_sections(args, ports)
    head = _generation_md(args, rep, "")
    cap = rep.summary_budget(sections, len(head))
    result_md = _generation_md(args, rep, args.post.fin.summary, summary_cap=cap)
    return rep.assemble_finalize_reply(
        sections + [rep.ReplySection("suite", result_md, protected=True)]
    )
