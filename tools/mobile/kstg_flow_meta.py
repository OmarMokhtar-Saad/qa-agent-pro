"""The per-step screen record a learned flow is saved with (see ``app_flows_meta``).

Each step's record carries the sk2 key of the screen it started on and the one it
ended on; the packets of one flow are cut from the same record list, in order.
"""

from __future__ import annotations


def packet_metas(recs: list, packets: list, reached: bool) -> list:
    """One meta per packet: the keys of the records that packet's steps came from.

    *reached* is True only when every record was kept and the run reached its goal.
    """
    metas: list = []
    start = 0
    for packet in packets:
        part = recs[start : start + len(packet)]
        start += len(packet)
        metas.append(
            {
                "before": [str(r.get("screen_key") or "") for r in part],
                "after": [str(r.get("after_screen_key") or "") for r in part],
                "reached": reached,
            }
        )
    return metas


def all_kept(recs: list, packets: list) -> bool:
    return sum(len(p) for p in packets) == len(recs)
