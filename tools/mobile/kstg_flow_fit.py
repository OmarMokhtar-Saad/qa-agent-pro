"""Fitting a learned route into the packet limit (see ``knowledge_stage_flows``).

A route over the limit is not refused: the leading installer / App Tester steps go
first (a replay never starts there once the app is installed); when it is still too
long, the first ``MAX_FLOW_PACKETS`` packets are kept and the note says how much of
the route they cover. The kept prefix is never marked as reaching the goal.
"""

from __future__ import annotations

from tools.mobile import kstg_flow_meta, kstg_flow_packets, kstg_flow_trim

_NOTE = (
    "flow needs %d packets (%d steps); saved the first %d packets (%d of %d steps), "
    "the rest is not covered"
)


def _cut(recs: list) -> tuple:
    """``(packets, limit)`` for *recs*: a fully kept route may take more packets."""
    packets = kstg_flow_packets.chunk([r["flow_action"] for r in recs])
    limit = kstg_flow_packets.MAX_FLOW_PACKETS
    if kstg_flow_meta.all_kept(recs, packets):
        limit = kstg_flow_packets.MAX_REACHED_PACKETS
    return packets, limit


def fit(recs: list) -> tuple:
    """``(recs, packets, note)``: what to save; no packets means nothing is saved."""
    packets, limit = _cut(recs)
    if len(packets) <= limit:
        return recs, packets, ""
    recs = kstg_flow_packets.replayable_from(kstg_flow_trim.without_approach(recs))
    packets, limit = _cut(recs)
    if len(packets) <= limit:
        return recs, packets, ""
    first = packets[: kstg_flow_packets.MAX_FLOW_PACKETS]
    kept = kstg_flow_packets.kept(first)
    note = _NOTE % (len(packets), len(recs), len(first), kept, len(recs))
    return recs, first, note
