"""One run, joined: the ``Evidence`` the report pages read."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile_evidence.evidence_stats import _StatsMixin
from tools.mobile_evidence.evidence_timing import _stamped
from tools.mobile_evidence.evidence_totals import _TotalsMixin


def _integrity_run(run):
    """What the run record says about the capture itself."""
    return {
        "malformed": run.get("malformedLines") or 0,
        "envClaimed": run.get("envClaimed"),
        "envFromTraffic": run.get("envFromTraffic"),
        "envDisagrees": bool(run.get("envDisagrees")),
        "hosts": run.get("hosts") or [],
        "source": run.get("source"),
        "turnsFromNarration": bool(run.get("turnsFromNarration")),
        "disabledStreams": list(run.get("disabled") or []),
    }


def _integrity_windows(windows):
    """The case ids whose window needed a caveat: clock unread, clipped, inverted."""
    return {
        "clockNotRead": [
            w["tc_id"] for w in windows if w.get("lo") is not None and not w["exact"]
        ],
        "clipped": [w["tc_id"] for w in windows if w.get("clipped")],
        "inverted": [w["tc_id"] for w in windows if w.get("inverted")],
    }


def _integrity_rows(rep, runs):
    """The stamped configs, the checkpoints and the calls that never returned."""
    configs = [
        _stamped(rep, c["appRunId"], c)
        for c in (rep.get("configs") or [])
        if c.get("appRunId") in runs
    ]
    checkpoints = [
        c for c in (rep.get("checkpoints") or []) if c.get("appRunId") in runs
    ]
    never = [
        _stamped(rep, b["appRunId"], b)
        for b in (rep.get("bindings") or [])
        if b.get("appRunId") in runs
        and b.get("endSeq") is None
        and not b.get("orphanResponse")
    ]
    return {"configs": configs, "checkpoints": checkpoints, "neverReturned": never}


class EvidenceFrame(NamedTuple):
    """The cases, their windows and the fence a run was cut by."""

    cases: object
    windows: object
    included: object
    excluded: object
    fence_note: object


class Evidence(_StatsMixin, _TotalsMixin):
    """One run, joined: the report, the fence, the case windows, the mapping."""

    __slots__ = (
        "report",
        "cases",
        "windows",
        "included",
        "excluded",
        "fence_note",
        "join",
        "source",
        "_by_turn",
        "_session",
    )

    def __init__(self, report, frame, jn):
        self.report = report
        self.cases = list(frame.cases or [])
        self.windows = frame.windows
        self.included = frame.included
        self.excluded = frame.excluded
        self.fence_note = frame.fence_note
        self.join = jn
        self.source = (report.get("run") or {}).get("source")
        self._by_turn = None
        self._session = None

    def window_of(self, tc_id):
        for w in self.windows:
            if w.get("tc_id") == str(tc_id):
                return w
        return None

    def integrity(self):
        """What the capture says about ITSELF: configs, checkpoints, never-returned calls, gaps."""
        runs = {r.get("appRunId") for r in self.included}
        rep = self.report
        return {
            **_integrity_rows(rep, runs),
            "unresolved": rep.get("unresolvedBindings") or [],
            **_integrity_run(rep.get("run") or {}),
            **_integrity_windows(self.windows),
            "joinNote": self.join.note,
        }
