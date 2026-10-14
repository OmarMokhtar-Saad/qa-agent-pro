"""The per-turn index of one run's records, and its out-of-turn session slots."""

from __future__ import annotations

from tools.mobile_evidence.evidence_join_model import Join
from tools.mobile_evidence.evidence_notes import STREAMS
from tools.mobile_evidence.evidence_slots import (
    _entry_key as _slot_entry_key,
)
from tools.mobile_evidence.evidence_slots import (
    _file_outside_turn,
    _session_slot,
)
from tools.mobile_evidence.evidence_timing import _stamped, _ts_of


def _run_of(x):
    """The app run a record belongs to, from the record or its ``detail``."""
    return x.get("appRunId") or (x.get("detail") or {}).get("appRunId")


def _sort_slots(out):
    """Order the entry and failed lists of every slot by ``seq``."""
    for slot in out:
        slot["entry"].sort(key=lambda b: b.get("seq") or 0)
        slot["failed"].sort(key=lambda b: b.get("seq") or 0)


class _IndexMixin:
    """``Evidence`` part one: the turn index, ``mine`` and the session slots.

    The annotations below declare what ``Evidence.__slots__`` supplies, so the mixin
    methods type-check; they hold no value, so ``__slots__ = ()`` stays valid.
    """

    __slots__ = ()

    report: dict
    windows: list
    included: list
    excluded: list
    join: Join
    _by_turn: dict | None
    _session: list | None

    def mine(self):
        return {tid for _tc, tid in self.join.pairs}

    def _synthetic_turn_of(self, run, rec):
        ts = _ts_of(self.report, run, rec.get("seq"))
        if ts is None:
            return None
        for w in self.windows:
            if w.get("lo") is not None and w["lo"] <= ts <= w["hi"]:
                return "case/" + w["tc_id"]
        return None

    def _turn_id_of(self, rec):
        tid = rec.get("turnId")
        if not tid and self.join.synthetic:
            tid = self._synthetic_turn_of(rec.get("appRunId"), rec)
        return tid

    def _index(self):
        if self._by_turn is not None:
            return self._by_turn
        runs = {r.get("appRunId") for r in self.included}
        idx: dict = {}
        for stream in STREAMS:
            for rec in self.report.get(stream) or []:
                run = rec.get("appRunId") or (rec.get("detail") or {}).get("appRunId")
                if run not in runs:
                    continue
                tid = rec.get("turnId")
                if not tid and self.join.synthetic:
                    tid = self._synthetic_turn_of(run, rec)
                if not tid:
                    continue
                slot = idx.setdefault(tid, {k: [] for k in STREAMS})
                slot[stream].append(_stamped(self.report, run, rec))
        self._by_turn = idx
        return idx

    def turn(self, turn_id):
        if not turn_id:
            return None
        return self._index().get(turn_id)

    def for_case(self, case):
        """One evidence dict per turn attributed to ``case``, in turn order; [] when none."""
        tc_id = str((case or {}).get("tc_id") or "")
        out = []
        for tid in self.join.turn_ids(tc_id):
            found = self.turn(tid)
            if found is not None:
                out.append(dict(found, turnId=tid))
        return out

    @staticmethod
    def _entry_key(verb, url):
        return _slot_entry_key(verb, url)

    def session(self):
        """Per app run: the work the app did outside every turn (start-up, sign-in, between cases)."""
        if self._session is not None:
            return self._session
        out = [_session_slot(r, self.report.get("manifests")) for r in self.included]
        index = {r["appRunId"]: r for r in out}
        for stream in STREAMS:
            for x in self.report.get(stream) or []:
                run = _run_of(x)
                if run not in index:
                    continue
                tid = x.get("turnId") or (
                    self._synthetic_turn_of(run, x) if self.join.synthetic else None
                )
                if tid:
                    continue
                _file_outside_turn(index[run], stream, _stamped(self.report, run, x))
        _sort_slots(out)
        self._session = out
        return out

    def ambiguous_records(self):
        """Every record of a turn the join called ambiguous -- for the run-level block."""
        ids = {tid for tid, _n in self.join.ambiguous}
        out = []
        for stream in STREAMS:
            for rec in self.report.get(stream) or []:
                if rec.get("turnId") in ids:
                    run = _run_of(rec)
                    out.append(dict(_stamped(self.report, run, rec), stream=stream))
        out.sort(key=lambda r: (r.get("ts") or 0, r.get("seq") or 0))
        return out
