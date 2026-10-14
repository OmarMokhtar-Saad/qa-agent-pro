"""Derived timings of the paired turns: spans, endpoints and models."""

from __future__ import annotations

from tools.mobile_evidence.evidence_index import _IndexMixin
from tools.mobile_evidence.evidence_timing import (
    _collect_record_spans,
    _collect_turn_spans,
    _derived_ms,
)


def _empty_spans():
    return {
        "llm": [],
        "llmfail": [],
        "api": [],
        "tool": [],
        "turn": [],
        "turnfail": [],
    }


def _endpoint_path(url):
    """The path of an endpoint url, without host or query; the url itself when it has none."""
    rest = url.split("://", 1)[1] if "://" in url else ""
    if "/" in rest:
        return "/" + rest.split("/", 1)[1].split("?")[0]
    return url or "(unknown)"


def _endpoint_row(key, path, b):
    """The empty aggregate of one ``(verb, path)`` endpoint."""
    return {
        "verb": key[0],
        "path": path,
        "calls": 0,
        "errors": 0,
        "samples": [],
        "binding": b.get("binding"),
    }


def _model_row(name):
    """The empty aggregate of one model."""
    return {"model": name, "calls": 0, "in": 0, "out": 0, "samples": []}


class _StatsMixin(_IndexMixin):
    """``Evidence`` part two: the derived duration samples."""

    __slots__ = ()

    def spans(self):
        """Derived duration samples, in milliseconds, by kind, over the paired turns."""
        mine = self.mine()
        out = _empty_spans()

        def owned(rec):
            return self._turn_id_of(rec) in mine

        _collect_record_spans(self.report, out, owned)
        _collect_turn_spans(self.report, out, mine)
        return out

    def endpoints(self):
        """Every endpoint reached in a paired turn, with its derived timings."""
        mine = self.mine()
        agg: dict = {}
        for b in self.report.get("bindings") or []:
            if (self._turn_id_of(b) or None) not in mine:
                continue
            path = _endpoint_path(b.get("url") or "")
            key = ((b.get("verb") or "CALL").upper(), path)
            r = agg.setdefault(key, _endpoint_row(key, path, b))
            r["calls"] += 1
            if b.get("ok") is False:
                r["errors"] += 1
            ms = _derived_ms(self.report, b.get("appRunId"), b)
            if ms is not None:
                r["samples"].append(float(ms))
        return list(agg.values())

    def models(self):
        """Every model that answered in a paired turn, with its derived timings."""
        mine = self.mine()
        agg: dict = {}
        for call in self.report.get("llm") or []:
            if (self._turn_id_of(call) or None) not in mine:
                continue
            tok = call.get("tokens") or {}
            name = tok.get("model") or call.get("model") or "(unrecorded)"
            r = agg.setdefault(name, _model_row(name))
            r["calls"] += 1
            r["in"] += tok.get("in") or 0
            r["out"] += tok.get("out") or 0
            ms = _derived_ms(self.report, call.get("appRunId"), call)
            if ms is not None:
                r["samples"].append(float(ms))
        return list(agg.values())
