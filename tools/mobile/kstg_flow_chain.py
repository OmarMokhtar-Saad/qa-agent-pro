"""Packet chains are pruned as a unit (see ``kstg_flow_prune``).

A flow saved in packets is ``<base>-p1`` .. ``<base>-pN``; the model replays them in
order, so a chain with one packet gone (or pruned) can only mislead. When any member
is dropped the whole chain goes, and so does a chain with a gap, unless one of its
members has replayed successfully (it is then real evidence, kept as it is). A chain
holding a packet just saved is never touched. Pure.
"""

from __future__ import annotations

from tools.mobile import flow_stats, kstg_flow_packets


def _chains(names: list) -> dict:
    """``{base: {n: name}}`` for the names that are packets."""
    found: dict = {}
    for name in names:
        hit = kstg_flow_packets.PACKET_RE.match(name)
        if hit:
            found.setdefault(hit.group("base"), {})[int(hit.group("n"))] = name
    return found


def _gapped(members: dict) -> bool:
    return sorted(members) != list(range(1, len(members) + 1))


def _proven(names: list, stats: dict) -> bool:
    return any(stats.get(name, {}).get("ok", 0) > 0 for name in names)


def doomed(items: list, gone: list, keep: set) -> list:
    """Names of the packet chains to drop with the *gone* names (*items*: name, template)."""
    stats = {name: flow_stats.counters(template) for name, template in items}
    out: list = []
    for members in _chains([name for name, _ in items]).values():
        names = list(members.values())
        if keep.intersection(names):
            continue
        hurt = bool(set(names) & set(gone))
        if hurt or (_gapped(members) and not _proven(names, stats)):
            out += names
    return out
