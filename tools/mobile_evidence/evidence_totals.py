"""What the paired turns spent, and what the excluded runs spent, for the evidence join."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile_evidence.evidence_index import _IndexMixin


class _LlmSpend(NamedTuple):
    calls: int
    tin: int
    tout: int
    ambiguous: int
    unplaceable: int


def _add_tokens(tok):
    """``(in, out)`` of one ``tokens`` dict, a missing count being 0."""
    return tok.get("in") or 0, tok.get("out") or 0


class _TotalsMixin(_IndexMixin):
    """``Evidence`` part three: token and row totals."""

    __slots__ = ()

    excluded: list

    def totals(self):
        """What the paired turns spent; ``ambiguous`` and ``unplaceable`` named separately."""
        mine = self.mine()
        amb = {tid for tid, _n in self.join.ambiguous}
        spend = self._paired_llm_spend(mine, amb)
        binds, unplaced_binds = self._count_turn_rows("bindings", mine)
        tools, unplaced_tools = self._count_turn_rows("tools", mine)
        return {
            "llm": spend.calls,
            "in": spend.tin,
            "out": spend.tout,
            "total": spend.tin + spend.tout,
            "bindings": binds,
            "tools": tools,
            "ambiguous": spend.ambiguous,
            "unplaceable": spend.unplaceable + unplaced_binds + unplaced_tools,
        }

    def _paired_llm_spend(self, mine, amb):
        tin = tout = calls = ambiguous = unplaceable = 0
        for call in self.report.get("llm") or []:
            tid = self._turn_id_of(call)
            if tid is None:
                unplaceable += 1
                continue
            if tid in amb:
                ambiguous += 1
                continue
            if tid not in mine:
                continue
            calls += 1
            tok = call.get("tokens") or {}
            tin += tok.get("in") or 0
            tout += tok.get("out") or 0
        return _LlmSpend(calls, tin, tout, ambiguous, unplaceable)

    def _count_turn_rows(self, key, mine):
        """(rows on a paired turn, rows with no placeable turn) for one report list."""
        matched = unplaceable = 0
        for row in self.report.get(key) or []:
            tid = self._turn_id_of(row)
            if tid is None:
                unplaceable += 1
            elif tid in mine:
                matched += 1
        return matched, unplaceable

    def excluded_totals(self):
        mine = self.mine()
        tin = tout = calls = binds = 0
        for call in self.report.get("llm") or []:
            tid = call.get("turnId")
            if tid is None or tid in mine:
                continue
            calls += 1
            t_in, t_out = _add_tokens(call.get("tokens") or {})
            tin, tout = tin + t_in, tout + t_out
        for b in self.report.get("bindings") or []:
            tid = b.get("turnId")
            if tid is not None and tid not in mine:
                binds += 1
        return {
            "runs": len(self.excluded),
            "llm": calls,
            "total": tin + tout,
            "bindings": binds,
        }
