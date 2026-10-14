"""The two input adapters: NDJSON structured log and logcat, both yielding event dicts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from tools.mobile_evidence.grammar_consts import logcat_line
from tools.mobile_evidence.grammar_pat import _literal_prefix, _prefix_re
from tools.mobile_evidence.grammar_values import _lines_of, _unflatten


def _ndjson_event(rec: dict, run_id, run_index: int) -> dict:
    """One structured record as the event dict every stream reads."""
    return {
        "appRunId": run_id,
        "cont": False,
        "runIndex": run_index,
        "seq": rec.get("seq"),
        "ts": rec.get("ts"),
        "monoNanos": rec.get("monoNanos"),
        "durationMs": rec.get("durationMs"),
        "level": rec.get("level"),
        "category": rec.get("category"),
        "msg": rec.get("msg", "") or "",
        "fields": rec.get("fields") or {},
        "sessionId": rec.get("sessionId"),
        "connectionId": rec.get("connectionId"),
        "turnId": rec.get("turnId"),
        "spanId": rec.get("spanId"),
    }


def _json_record(raw: str):
    """The record on one line, or None when it is malformed (not JSON, not an object)."""
    try:
        rec = json.loads(raw)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def _is_manifest(kind) -> bool:
    return kind == "sa.session.manifest" or (
        isinstance(kind, str) and kind.endswith(".session.manifest")
    )


@dataclass
class _Reader:
    """What one ``read_ndjson`` pass keeps: the records so far and the current app run."""

    manifests: list = field(default_factory=list)
    events: list = field(default_factory=list)
    checkpoints: list = field(default_factory=list)
    malformed: int = 0
    run_index: int = -1
    run_id: object = None

    def take(self, raw: str) -> None:
        """Sort one non-empty line into manifest, checkpoint, event or malformed."""
        rec = _json_record(raw)
        kind = rec.get("kind") if rec is not None else None
        if rec is None:
            self.malformed += 1
        elif _is_manifest(kind):
            self.run_index += 1
            self.run_id = rec.get("appRunId")
            self.manifests.append(rec)
        elif isinstance(kind, str) and kind.endswith(".session.checkpoint"):
            self.checkpoints.append(
                dict(rec, appRunId=self.run_id, runIndex=self.run_index)
            )
        else:
            self.events.append(_ndjson_event(rec, self.run_id, self.run_index))

    def result(self):
        """(manifests, events, malformed, checkpoints), the events in run then seq order."""
        self.events.sort(key=lambda e: (e["runIndex"], e["seq"] is None, e["seq"] or 0))
        return self.manifests, self.events, self.malformed, self.checkpoints


def read_ndjson(source):
    """The primary input. A manifest line opens each app run, then one event per line.

    One file may hold many app runs, each restarting ``seq`` at 0, so the app run is the
    ordering authority and ``seq`` only orders within it. Events before the first
    manifest get runIndex -1 and a null id: sorted first, kept visible, never dropped.
    Returns (manifests, events, malformed, checkpoints).
    """
    reader = _Reader()
    for raw in _lines_of(source):
        raw = raw.strip()
        if raw:
            reader.take(raw)
    return reader.result()


@dataclass(frozen=True)
class _LogcatCtx:
    """What a logcat read needs about the profile: its tag and its category prefix."""

    tag: str
    prefix: str
    prefix_re: re.Pattern


def _split_category(msg: str, prefix_re: re.Pattern):
    """(category, message) once the SDK prefix is stripped; (None, msg) when it has none."""
    pre = prefix_re.match(msg)
    if pre and pre.end() > 0:
        return pre.group("cat"), msg[pre.end() :]
    return None, msg


def _event_record(seq: int, m, parts: tuple) -> dict:
    """The event dict of one logcat line; ``parts`` is (category, msg, cont, fields)."""
    category, msg, cont, fields = parts
    return {
        "appRunId": None,
        "runIndex": 0,
        "cont": cont,
        "seq": seq,
        "ts": m.group("ts"),
        "monoNanos": None,
        "durationMs": None,
        "level": m.group("level"),
        "category": category,
        "msg": msg,
        "fields": fields,
        "sessionId": None,
        "connectionId": None,
        "turnId": None,
        "spanId": None,
    }


def _logcat_event(m, seq: int, ctx: _LogcatCtx) -> dict:
    """One logcat match as an event; a legacy line is not unflattened."""
    msg = m.group("msg")
    cont = bool(ctx.prefix) and not msg.startswith(ctx.prefix)
    category, msg = _split_category(msg, ctx.prefix_re)
    fields: dict = {}
    if category and category != "LEGACY":
        msg, fields = _unflatten(msg)
    return _event_record(seq, m, (category, msg, cont, fields))


def read_logcat(source, profile):
    """The fallback. Fewer fields resolve, and a multi-line body arrives already split.

    A line that does not open with the SDK's prefix is the TAIL of the line above it
    (``cont``); a legacy line carries no ``[CATEGORY]`` bracket and its prose is NOT
    unflattened.
    """
    tag = str(getattr(profile, "logcat_tag", "") or "")
    ctx = _LogcatCtx(tag, _literal_prefix(profile), _prefix_re(profile))
    events: list = []
    for raw in _lines_of(source):
        m = logcat_line(raw.rstrip("\n"))
        if not m or (tag and m.group("tag").strip() != tag):
            continue
        events.append(_logcat_event(m, len(events), ctx))
    return [], events, 0, []
