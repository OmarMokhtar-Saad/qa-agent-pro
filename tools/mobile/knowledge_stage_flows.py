"""Learning stage flows: a clean explore run becomes replayable app flows.

When an explore run ended with ``goal_reached`` (or a pass verdict) and its last step
ran as planned, the steps that worked and landed in the package under test are saved as
its flow, named from the normalised goal (``flows.json``, via ``app_flows``). A run
longer than one script is saved whole as ordered packets (``kstg_flow_packets``); a step
that cannot stand alone ends the usable prefix, which is still saved (not marked as
reaching the goal). Each flow carries the screen before and after every step, so the
server can match and auto-replay it. Typed values never enter it: a value is a
``{field}`` placeholder, a secret step keeps only its field name. A flow that reaches
the goal prunes the older ones that fall short. Never raises.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.mobile import (
    app_flows,
    kstg_flow_build,
    kstg_flow_cut,
    kstg_flow_dedupe,
    kstg_flow_gate,
    kstg_flow_merge,
    kstg_flow_meta,
    kstg_flow_packets,
    kstg_flow_prune,
    kstg_flow_sig,
)
from tools.mobile.knowledge_learn import StageResult

logger = logging.getLogger(__name__)


class _Packet(NamedTuple):
    name: str
    acts: list
    meta: dict


class _Outcome(NamedTuple):
    error: str  # why the flow was not saved, else ""
    note: str  # the staging note for the packet
    holder: str  # the flow now holding the packet: itself, or the one covering it


def _save(package: str, packet: _Packet) -> _Outcome:
    got = app_flows.template_from_script({"actions": packet.acts})
    if got.get("error"):
        return _Outcome(str(got["error"])[:120], "", "")
    meta = kstg_flow_merge.merged_meta(package, packet.name, packet.meta)
    template = dict(got["content"], meta=meta)
    verdict = kstg_flow_dedupe.judge(package, packet.name, template)
    if verdict.covered_by:
        note = "skipped %s, already covered by %s" % (packet.name, verdict.covered_by)
        return _Outcome("", note, verdict.covered_by)
    error = str(app_flows.save_flow(package, packet.name, template).get("error") or "")
    if error:  # the replaced flows stay when the new one could not be saved
        return _Outcome(error[:120], "", "")
    gone = kstg_flow_dedupe.drop(package, verdict.replaces)
    note = "saved " + packet.name + (" (replaced %s)" % ", ".join(gone) if gone else "")
    return _Outcome("", note, packet.name)


def _packets(acts: list) -> tuple:
    """``(packets, why)``: the replayable prefix packets, or why nothing is saved."""
    packets = kstg_flow_packets.chunk(acts)
    if not packets:
        return [], "the first step cannot be replayed on its own; nothing saved"
    if len(packets) > kstg_flow_packets.MAX_FLOW_PACKETS:
        limit = kstg_flow_packets.MAX_FLOW_PACKETS
        return [], "flow has %d steps, over the %d-packet limit; nothing saved" % (
            len(acts),
            limit,
        )
    return packets, ""


def _plan(goal: str, recs: list, packets: list) -> list:
    done = kstg_flow_meta.all_kept(recs, packets)
    metas = kstg_flow_meta.packet_metas(recs, packets, done)
    sig = kstg_flow_sig.of_records(recs)
    names = kstg_flow_packets.names(goal, len(packets), sig)
    metas = [dict(m, goal=kstg_flow_build.normalise(goal)) for m in metas]
    return [_Packet(n, p, m) for n, p, m in zip(names, packets, metas)]


def _write(package: str, plan: list, result: StageResult) -> list:
    """The flows holding the plan's packets (saved or covering); [] on any failure."""
    held: list = []
    for packet in plan:
        out = _save(package, packet)
        result.notes.append("%s: %s" % (package, out.error or out.note))
        if out.error:
            return []
        result.wrote += 1 if out.holder == packet.name else 0
        held.append(out.holder)
    return held


def _save_all(lc, goal_recs: tuple, result: StageResult) -> None:
    goal, recs = goal_recs[0], kstg_flow_cut.repair(goal_recs[1])
    refused = kstg_flow_gate.reject(recs)
    if refused:
        result.notes.append("%s: not saved, %s" % (lc.package, refused))
        return
    packets, why = _packets([r["flow_action"] for r in recs])
    if why:
        result.notes.append("%s: %s" % (lc.package, why))
        return
    kept = kstg_flow_packets.kept(packets)
    if kept < len(recs):
        note = "%s: kept first %d of %d steps" % (lc.package, kept, len(recs))
        result.notes.append(note)
    plan = _plan(goal, recs, packets)
    held = _write(lc.package, plan, result)
    if held and kept == len(recs):  # a covering flow holds a skipped packet: keep it
        kstg_flow_prune.prune(lc.package, held, kept)


def run(conn, lc) -> StageResult:
    goal = kstg_flow_build.reached(lc.run_id)
    if not goal or not kstg_flow_build.ended_clean(lc.steps):
        return StageResult("flows", notes=["no clean goal_reached run"])
    result = StageResult("flows")
    recs = kstg_flow_build.app_recs(lc.steps, lc.package)
    if len(recs) >= kstg_flow_build.MIN_STEPS:
        _save_all(lc, (goal, recs), result)
    return result
