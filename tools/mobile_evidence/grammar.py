"""Turn an app's captured log into structured streams -- with the vocabulary supplied.

Two inputs, one grammar. An app SDK may write a structured NDJSON event log to its
private storage and a prose narration to logcat; the same message text reaches both,
so both are read here, the structured shape preferred when present. Nothing in this
module knows what that prose LOOKS like: every pattern for the app's own narration --
the request/response pair stream, the model round-trip stream, the usage line, the
turn-start marker, the tool-invocation and tool-result lines, the flow-state and card
lines, the network line -- is compiled from a ``Profile`` (``profiles.py``) and looked up
by STREAM NAME. What lives here is the part that is the same for every app:

  * the two Android logcat line formats (threadtime and brief);
  * the shape of a category prefix (``<prefix> - [CAT]``), built from the profile's prefix;
  * the ``k=v k=v`` flattening a structured event undergoes on its way to logcat;
  * the body-policy notations (``[len N h:xxxx]`` hashed, ``...[+N chars]`` truncated,
    ``...(+N more)`` clipped);
  * the NDJSON record fields (``seq`` is the ordering authority, ``ts`` display only);
  * the algorithms: request/response pairing oldest-first per name, usage twin merging,
    turn attribution from the turn-start marker, network-line splicing by a voted clock
    offset, token totals.

A stream whose pattern the profile does not carry is skipped and NAMED in the parse
result's ``disabled`` list, so a report can say "this capture cannot show X" rather than
show nothing. Nothing here raises to a caller: ``build`` returns ``{"error", "content"}``.

Every pattern is looked up through :func:`_pat`; a profile that misses one disables that
stream only. The engine may carry no glyph, no right-to-left codepoint and no word of any
vendor's vocabulary -- a test greps this file for exactly that.
"""

from __future__ import annotations

import bisect
import collections
import datetime
import json
import re
from dataclasses import dataclass, field
from typing import Iterable

SCHEMA = "qa-agents.mobile-evidence.report/1"

#: The parse streams a profile may carry a pattern for, by name. The names are the
#: engine's; the text behind each is the profile's.
STREAMS = (
    "bind_req",
    "bind_res",
    "bind_err",
    "bind_retry",
    "call_req",
    "call_res",
    "llm_req",
    "llm_res",
    "llm_frame",
    "llm_err",
    "prompt_msg",
    "prompt_user",
    "usage",
    "note",
    "agent_turn",
    "agent_answer",
    "agent_fail",
    "tool_invoke",
    "tool_done",
    "flow_state",
    "flow_field",
    "card_push",
    "net",
    "hash_head",
    "served_model",
    "tool_call",
    "data_line",
)

# ── generic transport patterns (engine-owned) ──────────────────────────────────

#: One logcat line, in either of the two formats adb produces.
#:   threadtime  ``MM-DD HH:MM:SS.mmm  PID  TID L TAG: msg``   (``-v threadtime``)
#:   brief       ``MM-DD HH:MM:SS.mmm L/TAG( PID): msg``        (adb's default)
RE_LOGCAT = re.compile(
    r"^(?P<ts>\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEF]) (?P<tag>[^:]+?): (?P<msg>.*)$"
)
RE_LOGCAT_BRIEF = re.compile(
    r"^(?P<ts>\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+"
    r"(?P<level>[VDIWEF])/(?P<tag>[^(]+?)\(\s*(?P<pid>\d+)\): (?P<msg>.*)$"
)
#: ``MM-DD HH:MM:SS.mmm`` at the head of a logcat line; no year, no zone.
RE_SLICE_TS = re.compile(r"^(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\.(\d{3})")
#: A structured event flattened to logcat as ``msg k=v k=v``; split only where a
#: plausible field name starts, because a VALUE may hold spaces and ``=``.
RE_KV_BOUNDARY = re.compile(r"\s+(?=[A-Za-z][A-Za-z0-9_.]*=)")
#: Body-policy notations.
RE_BODY_HASHED = re.compile(r"^\[len (\d+) h:([0-9a-f]{4})\]$")
RE_BODY_TRUNC = re.compile(r"\u2026\[\+(\d+) chars\]$")
RE_TRUNC_TAIL = re.compile(r"\u2026\(\+(\d+) more\)$")

#: Named gaps every capture of this kind has; stated so a report can say them.
GAPS = [
    {
        "id": "httpStatusOnSuccess",
        "what": "HTTP status code for a successful request",
        "why": "the SDK logs the status only on the failure branch; the success branch "
        "logs the body alone, so a 200 and a 204 are indistinguishable here.",
    },
    {
        "id": "httpDuration",
        "what": "a measured duration for any request",
        "why": "nothing times the call; durations on this page are DERIVED from the clock "
        "on the request line and the response line.",
    },
    {
        "id": "engineSnapshot",
        "what": "engine / flow state snapshots",
        "why": "there is no state event; the only state visible is whatever the narrated "
        "note text says, which is UI prose rather than a machine record.",
    },
]


def logcat_line(line: str):
    """One parsed logcat line, whichever of the two formats it is in, or None."""
    return RE_LOGCAT.match(line) or RE_LOGCAT_BRIEF.match(line)


_META = set(r"\^$.|?*+()[]{}")


def _looks_like_regex(text: str) -> bool:
    return text.startswith("^") or any(ch in _META for ch in text)


def _literal_prefix(profile) -> str:
    """The literal head every SDK line opens with (``##``), whether the profile gave the
    literal or the whole prefix REGEX: the run of non-meta characters after a leading ``^``."""
    prefix = str(getattr(profile, "log_prefix", "") or "")
    if not _looks_like_regex(prefix):
        return prefix
    head = prefix[1:] if prefix.startswith("^") else prefix
    out = []
    for ch in head:
        if ch in _META:
            break
        out.append(ch)
    return "".join(out)


def _prefix_re(profile) -> re.Pattern:
    """``<prefix> - [CAT]`` -- the category prefix the SDK's logger writes.

    Accepts either a literal prefix (``##``), from which the shape is built, or the
    full prefix regex with a ``cat`` group, used as given.
    """
    prefix = str(getattr(profile, "log_prefix", "") or "")
    if not prefix:
        return re.compile(r"^(?:\[(?P<cat>[A-Z]+)\]\s*)?")
    if _looks_like_regex(prefix):
        try:
            pat = re.compile(prefix)
            if "cat" in pat.groupindex:
                return pat
        except re.error:
            pass
        prefix = _literal_prefix(profile)
        if not prefix:
            return re.compile(r"^(?:\[(?P<cat>[A-Z]+)\]\s*)?")
    return re.compile("^" + re.escape(prefix) + r"\s*-\s*(?:\[(?P<cat>[A-Z]+)\]\s*)?")


def _head_re(profile, compiled) -> re.Pattern:
    """The looser strip for a line whose dash belongs to the message itself."""
    pat = _pat(compiled, "hash_head")
    if pat is not None:
        return pat
    prefix = _literal_prefix(profile)
    if not prefix:
        return re.compile(r"^\s*")
    return re.compile("^" + re.escape(prefix) + r"\s*-?\s*")


def _pat(compiled, name: str):
    """The compiled pattern for one stream, or None when the profile disables it."""
    body = compiled if isinstance(compiled, dict) else {}
    patterns = body.get("patterns") or {}
    return patterns.get(name)


def _match(compiled, name: str, text: str):
    pat = _pat(compiled, name)
    return pat.match(text) if pat is not None else None


def _disabled_pairs(compiled) -> list:
    """The profile's own disabled list as ``[(stream, reason)]``, whichever shape it came in."""
    body = compiled if isinstance(compiled, dict) else {}
    raw = body.get("disabled") or {}
    if isinstance(raw, dict):
        return [(str(k), str(v)) for k, v in raw.items()]
    out = []
    for item in raw:
        try:
            out.append((str(item[0]), str(item[1])))
        except (TypeError, IndexError, KeyError):
            continue
    return out


def _unflatten(text: str):
    """``msg k=v k=v`` -> (msg, {k: v}). Only ever applied to a structured category."""
    parts = RE_KV_BOUNDARY.split(text.strip())
    msg, fields = parts[0], {}
    for part in parts[1:]:
        key, _, value = part.partition("=")
        fields[key] = value
    return msg, fields


def _body_shape(text):
    """How much of a body survived the body policy -- never guess it was verbatim."""
    if text is None:
        return None
    m = RE_BODY_HASHED.match(text.strip())
    if m:
        return {
            "policy": "NONE",
            "length": int(m.group(1)),
            "hash": m.group(2),
            "text": None,
        }
    m = RE_BODY_TRUNC.search(text)
    if m:
        return {"policy": "TRUNCATED", "omitted": int(m.group(1)), "text": text}
    return {"policy": "FULL", "text": text}


def _maybe_json(text):
    if not text:
        return None
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def _int_or_none(value):
    if value in (None, "?", ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _has_rtl(text: str) -> bool:
    return any("\u0590" <= ch <= "\u08ff" for ch in text or "")


def _lines_of(source) -> Iterable[str]:
    """A path or an iterable of lines, read the same way."""
    if isinstance(source, str):
        with open(source, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                yield raw
        return
    for raw in source or []:
        yield str(raw)


# ── input adapters: both produce the same list of event dicts ──────────────────


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


def read_ndjson(source):
    """The primary input. A manifest line opens each app run, then one event per line.

    One file may hold many app runs, each restarting ``seq`` at 0, so the app run is the
    ordering authority and ``seq`` only orders within it. Events before the first
    manifest get runIndex -1 and a null id: sorted first, kept visible, never dropped.
    Returns (manifests, events, malformed, checkpoints).
    """
    manifests, events, malformed, checkpoints = [], [], 0, []
    run_index, run_id = -1, None
    for raw in _lines_of(source):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(rec, dict):
            malformed += 1
            continue
        kind = rec.get("kind")
        if kind == "sa.session.manifest" or (
            isinstance(kind, str) and kind.endswith(".session.manifest")
        ):
            run_index += 1
            run_id = rec.get("appRunId")
            manifests.append(rec)
            continue
        if isinstance(kind, str) and kind.endswith(".session.checkpoint"):
            checkpoints.append(dict(rec, appRunId=run_id, runIndex=run_index))
            continue
        events.append(_ndjson_event(rec, run_id, run_index))
    events.sort(key=lambda e: (e["runIndex"], e["seq"] is None, e["seq"] or 0))
    return manifests, events, malformed, checkpoints


def read_logcat(source, profile):
    """The fallback. Fewer fields resolve, and a multi-line body arrives already split.

    A line that does not open with the SDK's prefix is the TAIL of the line above it
    (``cont``); a legacy line carries no ``[CATEGORY]`` bracket and its prose is NOT
    unflattened.
    """
    tag = str(getattr(profile, "logcat_tag", "") or "")
    prefix = _literal_prefix(profile)
    prefix_re = _prefix_re(profile)
    events, seq = [], 0
    for raw in _lines_of(source):
        m = logcat_line(raw.rstrip("\n"))
        if not m or (tag and m.group("tag").strip() != tag):
            continue
        msg = m.group("msg")
        cont = bool(prefix) and not msg.startswith(prefix)
        pre = prefix_re.match(msg)
        category, fields = None, {}
        if pre and pre.end() > 0:
            category = pre.group("cat")
            msg = msg[pre.end() :]
        if category and category != "LEGACY":
            msg, fields = _unflatten(msg)
        events.append(
            {
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
        )
        seq += 1
    return [], events, 0, []


# ── the parse ───────────────────────────────────────────────────────────────────


def _same_usage(a, b) -> bool:
    """Do two usage records describe the same round-trip?

    Compared on the counts and, when BOTH sides name one, the model. The prose line and
    the structured event are emitted back to back for one call; merging them costs one
    call in the count and never inflates the spend.
    """
    if not a or not b:
        return False
    if a.get("structured") == b.get("structured"):
        return False
    if not all(a.get(k) == b.get(k) for k in ("in", "out", "total")):
        return False
    return (
        a.get("model") is None
        or b.get("model") is None
        or a.get("model") == b.get("model")
    )


def _take_open(open_calls, name):
    """Oldest still-open request for ``name``, or None."""
    queue = open_calls.get(name)
    return queue.pop(0) if queue else None


def _names(value) -> list:
    if isinstance(value, str):
        return [value]
    return [str(v) for v in (value or [])]


def _msg_in(msg: str, names) -> bool:
    """Exact name, or a prefix when the configured name ends with a dot."""
    for name in _names(names):
        if name.endswith(".") and msg.startswith(name):
            return True
        if msg == name:
            return True
    return False


def runlog_lane(head: str, profile, compiled) -> str:
    """Which lane a narrated line belongs in. ``log`` is the lane of last resort."""
    lanes = getattr(profile, "runlog_lanes", None) or []
    for entry in lanes:
        try:
            lane, heads = entry[0], entry[1]
        except (TypeError, IndexError, KeyError):
            continue
        for h in _names(heads):
            if h and head.startswith(h):
                return str(lane)
    data_line = _pat(compiled, "data_line")
    if data_line is not None and data_line.match(head):
        return "net"
    return "log"


def _join_invocations(invocations, recovered) -> None:
    """One tool call from two half-witnesses: the invocation line carries the outcome,
    the model's own reply carries the real arguments. Edits ``invocations`` in place."""
    if not invocations:
        return
    pool = list(recovered)
    for entry in invocations:
        for i, cand in enumerate(pool):
            if cand["tool"] == entry["tool"]:
                entry["args"] = cand["args"]
                entry["argsRecovered"] = True
                pool.pop(i)
                break


def _pick_tools(tools, invocations, recovered) -> list:
    """The list the report calls its tools: the structured ones when there are any,
    else the invocations, else the recovered calls. Never a copy."""
    if invocations:
        return tools if tools else invocations
    if not tools and recovered:
        return recovered
    return tools


def _count_tools(tools, turns, runs) -> None:
    """Add each tool to the count of its turn and of its app run."""
    for t in tools:
        if t.get("turnId") and t["turnId"] in turns:
            turns[t["turnId"]]["tools"] += 1
        if t.get("appRunId") and t["appRunId"] in runs:
            runs[t["appRunId"]]["tools"] += 1


def _count_turns(turns, runs) -> None:
    """Add each turn to the count of its app run."""
    for t in turns.values():
        if t.get("appRunId") and t["appRunId"] in runs:
            runs[t["appRunId"]]["turns"] += 1


def _disabled_streams(compiled) -> list:
    """The profile's own disabled pairs, then every stream with no pattern."""
    disabled = _disabled_pairs(compiled)
    for name in STREAMS:
        if _pat(compiled, name) is None and not any(d[0] == name for d in disabled):
            disabled.append((name, "no pattern in the profile"))
    return disabled


@dataclass
class _ParseState:
    """Everything one ``parse`` call keeps across events."""

    bindings: list = field(default_factory=list)
    llm: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    runlog: list = field(default_factory=list)
    recovered: list = field(default_factory=list)
    utterances: list = field(default_factory=list)
    answers: list = field(default_factory=list)
    invocations: list = field(default_factory=list)
    flow_states: list = field(default_factory=list)
    cards: list = field(default_factory=list)
    configs: list = field(default_factory=list)
    open_binding: dict = field(default_factory=dict)
    open_llm: int | None = None
    last_usage: dict | None = None
    turns: dict = field(default_factory=dict)
    runs: dict = field(default_factory=dict)
    clock: dict = field(default_factory=dict)
    # A fresh sentinel per call, so the first event always starts a new run.
    run_index: object = field(default_factory=object)


@dataclass(frozen=True)
class _Line:
    """The fields of one event that every stream reads."""

    ev: dict
    seq: object
    ts: object
    run: str | None
    turn: object
    msg: str
    fields: dict
    cat: object


@dataclass(frozen=True)
class _Grammar:
    """The profile settings ``parse`` reads once, before the first event."""

    profile: object
    compiled: dict
    tool_cat: object
    cost_cat: object
    config_cat: object
    tool_msgs: object
    cost_msgs: object
    config_msgs: object
    bad_marks: list
    head_re: re.Pattern


def _grammar_of(profile, compiled) -> _Grammar:
    """Read the structured categories, run-log marks and head prefix of ``profile``."""
    structured = getattr(profile, "structured", None) or {}
    return _Grammar(
        profile=profile,
        compiled=compiled,
        tool_cat=structured.get("tool_category"),
        cost_cat=structured.get("cost_category"),
        config_cat=structured.get("config_category"),
        tool_msgs=structured.get("tool_msgs") or [],
        cost_msgs=structured.get("cost_msgs") or [],
        config_msgs=structured.get("config_msg") or [],
        bad_marks=_names(getattr(profile, "runlog_bad", None) or []),
        head_re=_head_re(profile, compiled),
    )


def _enter_run(state: _ParseState, ev) -> None:
    """At a ``runIndex`` change, forget the open calls of the run before."""
    if ev.get("runIndex") != state.run_index:
        state.run_index = ev.get("runIndex")
        state.open_binding, state.open_llm, state.last_usage = {}, None, None


def _line_of(ev, run_index) -> _Line:
    """The event's fields, with its app run named from ``run_index`` when it has none."""
    run = ev.get("appRunId") or (
        "run-%s" % run_index if run_index is not None else None
    )
    return _Line(
        ev=ev,
        seq=ev.get("seq"),
        ts=ev.get("ts"),
        run=run,
        turn=ev.get("turnId"),
        msg=ev.get("msg") or "",
        fields=ev.get("fields") or {},
        cat=ev.get("category"),
    )


def _record_run(state: _ParseState, line: _Line) -> None:
    """Open or extend the app run record of ``line`` and clock its timestamp."""
    run, seq, ts = line.run, line.seq, line.ts
    if not run:
        return
    r = state.runs.setdefault(
        run,
        {
            "appRunId": run,
            "runIndex": state.run_index,
            "firstSeq": seq,
            "lastSeq": seq,
            "firstTs": ts,
            "lastTs": ts,
            "llm": 0,
            "bindings": 0,
            "tools": 0,
            "errors": 0,
            "turns": 0,
        },
    )
    r["lastSeq"] = seq
    # A structured log carries epoch ms; a logcat capture carries the raw
    # ``MM-DD HH:MM:SS.mmm`` stamp, which orders lexicographically within a
    # year and is made numeric by ``evidence.normalise_clock``. Either way the
    # clock is recorded, so a logcat-only run still has a window.
    if isinstance(ts, (int, float, str)) and not isinstance(ts, bool) and ts != "":
        try:
            if r["firstTs"] is None or ts < r["firstTs"]:
                r["firstTs"] = ts
            if r["lastTs"] is None or ts > r["lastTs"]:
                r["lastTs"] = ts
        except TypeError:
            pass
        state.clock.setdefault(run, {})[str(seq)] = ts


def _record_turn(state: _ParseState, line: _Line) -> None:
    """Open or extend the turn record of ``line``."""
    turn, seq = line.turn, line.seq
    if not turn:
        return
    t = state.turns.setdefault(
        turn,
        {
            "turnId": turn,
            "connectionId": line.ev.get("connectionId"),
            "appRunId": line.run,
            "firstSeq": seq,
            "lastSeq": seq,
            "llm": 0,
            "bindings": 0,
            "tools": 0,
            "errors": 0,
        },
    )
    t["lastSeq"] = seq


def _bump(state: _ParseState, line: _Line, key: str) -> None:
    """Add one to ``key`` on the turn and the app run of ``line``, when it has them."""
    if line.turn:
        state.turns[line.turn][key] += 1
    if line.run:
        state.runs[line.run][key] += 1


def _parse_result(state: _ParseState, merged: int, disabled: list) -> dict:
    """Join and count what the events left in ``state``, in the result's key order."""
    unresolved = [
        state.bindings[i]["binding"] for q in state.open_binding.values() for i in q
    ]
    _join_invocations(state.invocations, state.recovered)
    tools = _pick_tools(state.tools, state.invocations, state.recovered)
    _count_tools(tools, state.turns, state.runs)
    _count_turns(state.turns, state.runs)
    turns, runs = state.turns, state.runs
    return {
        "clock": state.clock,
        "bindings": state.bindings,
        "llm": state.llm,
        "tools": tools,
        "notes": state.notes,
        "errors": state.errors,
        "runlog": state.runlog,
        "utterances": state.utterances,
        "answers": state.answers,
        "flowStates": state.flow_states,
        "cards": state.cards,
        "configs": state.configs,
        "turns": [
            turns[k] for k in sorted(turns, key=lambda k: turns[k]["firstSeq"] or 0)
        ],
        "appRuns": sorted(runs.values(), key=lambda r: r["runIndex"]),
        "unresolvedBindings": unresolved,
        "mergedNetworkLines": merged,
        "disabled": disabled,
    }


def _on_tool_event(state: _ParseState, line: _Line, gram: _Grammar) -> bool:
    """Record a structured tool event; True when ``line`` was one."""
    msg, fields = line.msg, line.fields
    if not (
        gram.tool_cat and line.cat == gram.tool_cat and _msg_in(msg, gram.tool_msgs)
    ):
        return False
    entry = {
        "seq": line.seq,
        "turnId": line.turn,
        "appRunId": line.run,
        "tool": fields.get("tool"),
        "status": msg.split(".", 1)[1] if "." in msg else msg,
        "kind": fields.get("kind"),
        "args": _body_shape(fields.get("body.args")),
        "durationMs": line.ev.get("durationMs"),
    }
    state.tools.append(entry)
    _bump(state, line, "tools")
    if entry["status"] == "failed":
        state.errors.append({"seq": line.seq, "kind": "tool", "detail": entry})
        _bump(state, line, "errors")
    return True


def _attach_usage(state: _ParseState, line: _Line, usage: dict) -> None:
    """Attach ``usage`` to the open llm call, merge it into the last, or add an orphan."""
    if state.open_llm is not None and state.llm[state.open_llm].get("tokens") is None:
        state.llm[state.open_llm]["tokens"] = usage
        state.llm[state.open_llm]["endSeq"] = line.seq
        state.last_usage = state.llm[state.open_llm]["tokens"]
    elif _same_usage(state.last_usage, usage):
        # The same round-trip, reported twice (prose then structured): the
        # structured form replaces the prose in place, never appends.
        state.last_usage.update(usage)
    else:
        state.llm.append(
            {
                "seq": line.seq,
                "turnId": line.turn,
                "appRunId": line.run,
                "model": usage["model"],
                "tokens": usage,
                "promptMessages": [],
                "orphanUsage": True,
            }
        )
        state.last_usage = state.llm[-1]["tokens"]


def _on_cost_event(state: _ParseState, line: _Line, gram: _Grammar) -> bool:
    """Record a structured cost event as llm usage; True when ``line`` was one."""
    msg, fields = line.msg, line.fields
    if not (
        gram.cost_cat and line.cat == gram.cost_cat and _msg_in(msg, gram.cost_msgs)
    ):
        return False
    usage = {
        "seq": line.seq,
        "turnId": line.turn,
        "appRunId": line.run,
        "source": msg,
        "model": fields.get("model"),
        "requested": fields.get("requested"),
        "in": _int_or_none(fields.get("promptTokens")),
        "out": _int_or_none(fields.get("responseTokens")),
        "total": _int_or_none(fields.get("totalTokens")),
        "unattributed": _int_or_none(fields.get("unattributed")),
        "finish": fields.get("finishReason"),
        "structured": True,
    }
    _attach_usage(state, line, usage)
    state.open_llm = None
    return True


def _on_config_event(state: _ParseState, line: _Line, gram: _Grammar) -> bool:
    """Record a structured config event; True when ``line`` was one."""
    if not (
        gram.config_cat
        and line.cat == gram.config_cat
        and _msg_in(line.msg, gram.config_msgs)
    ):
        return False
    state.configs.append(
        {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "fields": dict(line.fields),
        }
    )
    return True


def _on_structured(state: _ParseState, line: _Line, gram: _Grammar) -> bool:
    """Try the tool, cost and config arms in that order; True when one took ``line``."""
    return (
        _on_tool_event(state, line, gram)
        or _on_cost_event(state, line, gram)
        or _on_config_event(state, line, gram)
    )


def _on_bind_req(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Open a narrated binding call from a ``bind_req`` match; always True."""
    g = m.groupdict()
    state.bindings.append(
        {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "binding": g.get("name"),
            "verb": g.get("verb"),
            "url": g.get("url"),
            "args": _maybe_json(g.get("args")) or g.get("args"),
            "forDependentSuffix": g.get("dep"),
            "status": None,
            "statusKnown": False,
            "ok": None,
            "response": None,
            "retried": False,
            "endSeq": None,
        }
    )
    state.open_binding.setdefault(g.get("name"), []).append(len(state.bindings) - 1)
    _bump(state, line, "bindings")
    return True


def _on_call_req(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Open a shorthand call from a ``call_req`` match; always True."""
    g = m.groupdict()
    target = (g.get("target") or "").strip()
    is_url = target.startswith(("http://", "https://"))
    state.bindings.append(
        {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "binding": g.get("name"),
            "verb": g.get("verb"),
            "url": target if is_url else None,
            "target": None if is_url else target,
            "args": g.get("args"),
            "retried": False,
            "ok": None,
            "status": None,
            "statusKnown": False,
            "response": None,
            "endSeq": None,
        }
    )
    state.open_binding.setdefault("sh:" + str(g.get("name")), []).append(
        len(state.bindings) - 1
    )
    _bump(state, line, "bindings")
    return True


def _on_call_res(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Close the open shorthand call of a ``call_res`` match in place.

    False when no call is open: an unpaired shorthand response is NOT a call, and
    falls through to the narrated lane it has always been in.
    """
    g = m.groupdict()
    idx = _take_open(state.open_binding, "sh:" + str(g.get("name")))
    status, body = g.get("status"), g.get("body")
    summary = (g.get("summary") or "").strip()
    entry = {
        "text": body if body else summary,
        "omitted": 0,
        "json": _maybe_json(body) if body else None,
    }
    if idx is None:
        return False
    state.bindings[idx].update(
        {
            "ok": True,
            "status": int(status) if status else None,
            "statusKnown": bool(status),
            "summary": summary,
            "response": entry,
            "endSeq": line.seq,
        }
    )
    return True


def _on_bind_res(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Close the open binding of a ``bind_res`` match in place, or add an orphan; True."""
    g = m.groupdict()
    idx = _take_open(state.open_binding, g.get("name"))
    body = g.get("body") or ""
    clipped = RE_TRUNC_TAIL.search(body)
    entry = {
        "text": RE_TRUNC_TAIL.sub("", body) if clipped else body,
        "omitted": int(clipped.group(1)) if clipped else 0,
        "json": _maybe_json(RE_TRUNC_TAIL.sub("", body)),
    }
    target = (
        state.bindings[idx]
        if idx is not None
        else {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "binding": g.get("name"),
            "verb": None,
            "url": None,
            "args": None,
            "orphanResponse": True,
            "retried": False,
        }
    )
    # Success is known; the exact 2xx is NOT -- never a fabricated 200.
    target.update(
        {
            "ok": True,
            "status": None,
            "statusKnown": False,
            "response": entry,
            "endSeq": line.seq,
        }
    )
    if idx is None:
        state.bindings.append(target)
        _bump(state, line, "bindings")
    return True


def _on_bind_err(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Fail the open binding of a ``bind_err`` match, or add an orphan; True.

    The error's ``detail`` is the binding object itself, never a copy.
    """
    g = m.groupdict()
    idx = _take_open(state.open_binding, g.get("name"))
    detail = {
        "ok": False,
        "status": _int_or_none(g.get("status")),
        "statusKnown": g.get("status") is not None,
        "endSeq": line.seq,
        "response": {
            "text": g.get("body"),
            "omitted": None,
            "json": _maybe_json(g.get("body")),
        },
    }
    if idx is not None:
        state.bindings[idx].update(detail)
        state.errors.append(
            {"seq": line.seq, "kind": "binding", "detail": state.bindings[idx]}
        )
    else:
        orphan = {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "binding": g.get("name"),
            "verb": None,
            "url": None,
            "args": None,
            "retried": False,
            "orphanResponse": True,
        }
        orphan.update(detail)
        state.bindings.append(orphan)
        state.errors.append({"seq": line.seq, "kind": "binding", "detail": orphan})
        _bump(state, line, "bindings")
    _bump(state, line, "errors")
    return True


def _on_bind_retry(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Mark the oldest open call of a ``bind_retry`` match as retried; always True."""
    queue = state.open_binding.get(m.groupdict().get("name"))
    if queue:
        state.bindings[queue[0]]["retried"] = True
    return True


def _prompt_tail(state: _ParseState, tail: str, compiled) -> None:
    """Add the prompt messages on ``tail`` to the open llm call.

    This is the structured path: the messages ride on the same record. A body
    that spans lines is appended to the message before it.
    """
    for piece in tail.split("\n"):
        _p = _match(compiled, "prompt_msg", piece)
        if _p:
            state.llm[state.open_llm]["promptMessages"].append(
                _body_shape(_p.group("body"))
            )
        elif state.llm[state.open_llm]["promptMessages"] and piece.strip():
            _prev = state.llm[state.open_llm]["promptMessages"][-1]
            if isinstance(_prev, dict) and isinstance(_prev.get("text"), str):
                _prev["text"] += "\n" + piece


def _on_llm_req(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Open an llm call from an ``llm_req`` match; always True."""
    g = m.groupdict()
    state.llm.append(
        {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "model": g.get("model"),
            "stream": bool(g.get("stream")),
            "msgs": _int_or_none(g.get("msgs")),
            "tools": _int_or_none(g.get("tools")),
            "promptMessages": [],
            "frames": 0,
            "response": None,
            "tokens": None,
            "error": None,
            "endSeq": None,
        }
    )
    state.open_llm = len(state.llm) - 1
    _prompt_tail(state, line.msg[m.end() :], gram.compiled)
    _bump(state, line, "llm")
    return True


def _on_prompt_msg(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Add a ``prompt_msg`` match to the open llm call; False when no call is open."""
    if state.open_llm is None:
        return False
    state.llm[state.open_llm]["promptMessages"].append(_body_shape(m.group("body")))
    return True


def _on_llm_frame(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Count an ``llm_frame`` match on the open llm call; False when no call is open."""
    if state.open_llm is None:
        return False
    state.llm[state.open_llm]["frames"] += 1
    return True


def _on_llm_res(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Set the open call's response and recover the tool calls in it; always True."""
    body = m.groupdict().get("body") or ""
    if state.open_llm is not None:
        state.llm[state.open_llm]["response"] = _body_shape(body)
        state.llm[state.open_llm]["endSeq"] = line.seq
    tool_call = _pat(gram.compiled, "tool_call")
    if tool_call is not None:
        for call in tool_call.finditer(body):
            state.recovered.append(
                {
                    "seq": line.seq,
                    "turnId": line.turn,
                    "appRunId": line.run,
                    "tool": call.group("tool"),
                    "status": "called",
                    "kind": None,
                    "args": _body_shape(call.group("args")),
                    "durationMs": None,
                    "argsRecovered": True,
                }
            )
    return True


def _on_llm_err(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Record an ``llm_err`` match; it closes the open call only before a response."""
    g = m.groupdict()
    err = {"class": g.get("cls"), "message": g.get("msg")}
    if state.open_llm is not None and state.llm[state.open_llm].get("response") is None:
        state.llm[state.open_llm]["error"] = err
        state.llm[state.open_llm]["endSeq"] = line.seq
        state.open_llm = None
    state.errors.append(
        {
            "seq": line.seq,
            "kind": "llm",
            "turnId": line.turn,
            "appRunId": line.run,
            "detail": err,
        }
    )
    _bump(state, line, "errors")
    return True


def _on_usage(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Record a prose ``usage`` match; the open llm call stays open. Always True."""
    g = m.groupdict()
    usage = {
        "seq": line.seq,
        "turnId": line.turn,
        "source": "prose",
        "model": g.get("model"),
        "requested": g.get("requested"),
        "in": _int_or_none(g.get("in")),
        "out": _int_or_none(g.get("out")),
        # ``total`` is authoritative -- never recomputed as in+out.
        "total": _int_or_none(g.get("total")),
        "unattributed": _int_or_none(g.get("unattr")),
        "finish": g.get("finish"),
        "structured": False,
    }
    _attach_usage(state, line, usage)
    return True


def _on_note(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Record a ``note`` match with its reading direction. Always True."""
    g = m.groupdict()
    state.notes.append(
        {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "noteId": g.get("id"),
            "text": g.get("text"),
            "lang": "rtl" if _has_rtl(line.msg) else "ltr",
        }
    )
    return True


def _on_agent_turn(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Record an ``agent_turn`` match as an utterance. Always True."""
    g = m.groupdict()
    state.utterances.append(
        {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "text": g.get("text"),
            "history": _int_or_none(g.get("history")),
        }
    )
    return True


def _on_agent_answer(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Record an ``agent_answer`` match. Always True."""
    state.answers.append(
        {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "text": m.groupdict().get("text"),
        }
    )
    return True


def _on_tool_invoke(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Open a narrated tool invocation from a ``tool_invoke`` match. Always True."""
    g = m.groupdict()
    state.invocations.append(
        {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "tool": g.get("tool"),
            "status": "called",
            "kind": None,
            "durationMs": None,
            "args": None,
            "loggedArgs": g.get("args"),
            "resultText": None,
            "endSeq": None,
        }
    )
    return True


def _on_tool_done(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Close the newest open invocation of the same tool, if any. Always True."""
    g = m.groupdict()
    for entry in reversed(state.invocations):
        if entry["tool"] == g.get("tool") and entry["resultText"] is None:
            entry["resultText"] = g.get("result")
            entry["status"] = "done"
            entry["endSeq"] = line.seq
            break
    return True


def _on_flow_state(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Record a ``flow_state`` match: its head fields, then the fixed keys. Always True."""
    g = m.groupdict()
    rest = g.get("rest") or ""
    head, _, note = rest.partition("note=")
    field_pat = _pat(gram.compiled, "flow_field")
    record = dict(field_pat.findall(head)) if field_pat is not None else {}
    record.update(
        {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "tool": g.get("tool"),
            "note": note.strip() or None,
        }
    )
    state.flow_states.append(record)
    return True


def _on_card_push(state: _ParseState, line: _Line, m, gram: _Grammar) -> bool:
    """Record a ``card_push`` match. Always True."""
    g = m.groupdict()
    state.cards.append(
        {
            "seq": line.seq,
            "turnId": line.turn,
            "appRunId": line.run,
            "kind": g.get("kind"),
            "replaced": g.get("replaced") == "true",
        }
    )
    return True


# The prose matchers, tried in this order after the structured events. The order is the
# behaviour: the first handler that returns True claims the line. ``call_res`` (unpaired),
# ``prompt_msg`` and ``llm_frame`` (no open call) return False, so the later ones still run.
_PROSE_ARMS = (
    ("bind_req", _on_bind_req),
    ("call_req", _on_call_req),
    ("call_res", _on_call_res),
    ("bind_res", _on_bind_res),
    ("bind_err", _on_bind_err),
    ("bind_retry", _on_bind_retry),
    ("llm_req", _on_llm_req),
    ("prompt_msg", _on_prompt_msg),
    ("llm_frame", _on_llm_frame),
    ("llm_res", _on_llm_res),
    ("llm_err", _on_llm_err),
    ("usage", _on_usage),
    ("note", _on_note),
    ("agent_turn", _on_agent_turn),
    ("agent_answer", _on_agent_answer),
    ("tool_invoke", _on_tool_invoke),
    ("tool_done", _on_tool_done),
    ("flow_state", _on_flow_state),
    ("card_push", _on_card_push),
)


def _on_failure(state: _ParseState, line: _Line, gram: _Grammar) -> None:
    """Record an ``agent_fail`` match, else an error-level line, as an error."""
    m = _match(gram.compiled, "agent_fail", line.msg)
    if m:
        state.errors.append(
            {
                "seq": line.seq,
                "kind": "agent",
                "turnId": line.turn,
                "appRunId": line.run,
                "detail": {"message": (m.groupdict().get("msg") or "").strip()},
            }
        )
        _bump(state, line, "errors")
    elif line.ev.get("level") in ("E", "ERROR"):
        state.errors.append(
            {
                "seq": line.seq,
                "kind": "log",
                "turnId": line.turn,
                "appRunId": line.run,
                "detail": {"category": line.cat, "message": line.msg},
            }
        )
        _bump(state, line, "errors")


def _runlog_entry(line: _Line, gram: _Grammar) -> dict | None:
    """The run-log record of a line no matcher claimed, or None for a continuation or no head."""
    if line.ev.get("cont"):
        return None
    head, _sep, rest = line.msg.partition("\n")
    head = gram.head_re.sub("", head, count=1).strip()
    if not head:
        return None
    net = _match(gram.compiled, "net", head)
    ng = net.groupdict() if net else {}
    return {
        "seq": line.seq,
        "turnId": line.turn,
        "appRunId": line.run,
        "lane": runlog_lane(head, gram.profile, gram.compiled),
        "head": head,
        "body": rest.strip() or None,
        "verb": ng.get("verb"),
        "url": ng.get("url"),
        "outcome": ng.get("outcome"),
        "bad": any(b in head for b in gram.bad_marks),
        "category": line.cat,
        "level": line.ev.get("level"),
    }


def _parse_event(state: _ParseState, ev, gram: _Grammar) -> None:
    """Read one event: bookkeeping, then the first stream that claims it, else the run log."""
    _enter_run(state, ev)
    line = _line_of(ev, state.run_index)
    _record_run(state, line)
    _record_turn(state, line)
    # Structured events first: they carry fields the prose cannot.
    if _on_structured(state, line, gram):
        return
    for name, handler in _PROSE_ARMS:
        m = _match(gram.compiled, name, line.msg)
        if m and handler(state, line, m, gram):
            return
    _on_failure(state, line, gram)
    # The fall-through stream: every narrated line no matcher claimed, kept whole.
    entry = _runlog_entry(line, gram)
    if entry is not None:
        state.runlog.append(entry)


def parse(events, logcat_lines, profile, compiled) -> dict:
    """Every stream the profile's grammar can read out of ``events``.

    ``logcat_lines`` (optional) is the logcat capture beside a structured log: its network
    stream is spliced in first (see :func:`merge_logcat_network`). The result's
    ``disabled`` lists every stream the profile carried no pattern for.
    """
    events = list(events or [])
    merged = 0
    if logcat_lines:
        merged = merge_logcat_network(events, logcat_lines, profile, compiled)
    gram, state = _grammar_of(profile, compiled), _ParseState()
    for ev in events:
        _parse_event(state, ev, gram)
    return _parse_result(state, merged, _disabled_streams(compiled))


# ── the system prompt, reassembled at the LINE level ───────────────────────────


def system_prompts(source, profile):
    """Every system prompt in a logcat slice, as (text, complete, seq) triples.

    The markers that open a prompt, open the next message and close a prompt come from
    ``profile.markers`` (``sys_mark``, ``next_msg``, ``sys_end``); without them there is
    nothing to reassemble and the result is empty. ``complete`` is False for a prompt the
    logger's line cap cut.
    """
    markers = getattr(profile, "markers", None) or {}
    sys_mark = markers.get("sys_mark")
    next_msg = markers.get("next_msg")
    sys_end = markers.get("sys_end")
    if not (sys_mark and next_msg and sys_end):
        return []
    return _reassemble_prompts(
        _logcat_messages(source, profile), sys_mark, next_msg, sys_end
    )


def _logcat_messages(source, profile) -> list:
    """The (seq, message) pairs of a logcat slice, tag-filtered and prefix-stripped."""
    tag = str(getattr(profile, "logcat_tag", "") or "")
    prefix_re = _prefix_re(profile)
    msgs = []
    for raw in _lines_of(source):
        m = logcat_line(raw.rstrip("\n"))
        if not m or (tag and m.group("tag").strip() != tag):
            continue
        msg = m.group("msg")
        pre = prefix_re.match(msg)
        msgs.append((len(msgs), msg[pre.end() :] if pre else msg))
    return msgs


def _reassemble_prompts(msgs, sys_mark, next_msg, sys_end) -> list:
    """Join the messages between the open and close markers into (text, complete, seq)."""
    out, buf, start = [], None, None
    for seq, s in msgs:
        if buf is None:
            i = s.find(sys_mark)
            if i >= 0:
                head = s[i + len(sys_mark) :]
                if sys_end in head:
                    out.append((head.split(sys_end)[0], True, seq))
                else:
                    buf, start = [head], seq
        elif s.startswith(next_msg):
            out.append(("\n".join(buf), False, start))
            buf = None
        elif sys_end in s:
            buf.append(s.split(sys_end)[0])
            out.append(("\n".join(buf), True, start))
            buf = None
        else:
            buf.append(s)
    if buf is not None:
        out.append(("\n".join(buf), False, start))
    return out


def env_from_traffic(bindings, profile):
    """(effective env, [hosts seen]) -- None when nothing left the device."""
    by_host = getattr(profile, "env_by_host", None) or {}
    hosts = []
    for b in bindings or []:
        url = b.get("url") or ""
        if "://" not in url:
            continue
        host = url.split("://", 1)[1].split("/", 1)[0]
        if host not in hosts:
            hosts.append(host)
    if not hosts:
        return None, []
    envs = {by_host.get(h) for h in hosts}
    envs.discard(None)
    if not envs:
        return None, hosts
    return (envs.pop() if len(envs) == 1 else "mixed"), hosts


def turn_of_seq(parsed, seq, app_run=None):
    """Which turn a ``seq`` falls in, for a stream ``parse`` never saw."""
    if seq is None:
        return None
    for t in parsed.get("turns") or []:
        if app_run is not None and t.get("appRunId") != app_run:
            continue
        if (t.get("firstSeq") or 0) <= seq <= (t.get("lastSeq") or 0):
            return t["turnId"]
    return None


def _turn_bounds(by_run: dict):
    """Per app run the (start, end, turnId) bounds, and the fresh turn record of each."""
    bounds_by_run, turns = {}, {}
    for run, utts in by_run.items():
        utts.sort(key=lambda u: u.get("seq") or 0)
        bounds = []
        for i, u in enumerate(utts):
            start = u.get("seq") or 0
            end = (utts[i + 1].get("seq") or 0) - 1 if i + 1 < len(utts) else None
            tid = "%s/t%d" % (run, i + 1) if run else "turn-%d" % (i + 1)
            bounds.append((start, end, tid))
            turns[tid] = {
                "turnId": tid,
                "connectionId": None,
                "appRunId": run,
                "index": i + 1,
                "firstSeq": start,
                "lastSeq": start,
                "llm": 0,
                "bindings": 0,
                "tools": 0,
                "errors": 0,
            }
        bounds_by_run[run] = bounds
    return bounds_by_run, turns


def _bounded_turn(bounds_by_run: dict, run, seq):
    """The turn id whose bounds hold ``seq`` in ``run``, or None."""
    if seq is None:
        return None
    for start, end, tid in bounds_by_run.get(run) or ():
        if seq >= start and (end is None or seq <= end):
            return tid
    return None


def _run_of(x: dict):
    return x.get("appRunId") or (x.get("detail") or {}).get("appRunId")


def _stamp_turn_ids(
    parsed: dict, by_run: dict, bounds_by_run: dict, turns: dict
) -> None:
    """Write ``turnId`` on every stream item and keep each turn's counts and last seq."""
    counted = {"llm": "llm", "bindings": "bindings", "tools": "tools"}
    for key in (
        "llm",
        "bindings",
        "tools",
        "answers",
        "flowStates",
        "cards",
        "notes",
        "runlog",
    ):
        for x in parsed.get(key) or []:
            tid = _bounded_turn(bounds_by_run, _run_of(x), x.get("seq"))
            x["turnId"] = tid
            if tid:
                t = turns[tid]
                t["lastSeq"] = max(t["lastSeq"], x.get("seq") or 0)
                if key in counted:
                    t[counted[key]] += 1
    for utts in by_run.values():
        for u in utts:
            u["turnId"] = _bounded_turn(bounds_by_run, u.get("appRunId"), u.get("seq"))
    for err in parsed.get("errors") or []:
        tid = _bounded_turn(bounds_by_run, _run_of(err), err.get("seq"))
        err["turnId"] = tid
        if tid:
            turns[tid]["errors"] += 1


def attribute_turns(parsed):
    """Turn boundaries from the SDK's own turn-start marker, when it stamped no turnId.

    One app run at a time: ``seq`` restarts per run. Everything between one marker and the
    next belongs to that turn; everything before a run's first marker is start-up and
    stays unattributed. A capture with real turn ids is left exactly as it is.
    """
    if parsed.get("turns") or not parsed.get("utterances"):
        return parsed
    by_run: dict = {}
    for u in parsed["utterances"]:
        by_run.setdefault(u.get("appRunId"), []).append(u)
    order = {
        r.get("appRunId"): r.get("runIndex") for r in (parsed.get("appRuns") or [])
    }
    bounds_by_run, turns = _turn_bounds(by_run)
    _stamp_turn_ids(parsed, by_run, bounds_by_run, turns)
    parsed["turns"] = sorted(
        turns.values(),
        key=lambda t: (
            order.get(t["appRunId"]) if order.get(t["appRunId"]) is not None else -1,
            t["firstSeq"],
        ),
    )
    per_run: dict = {}
    for t in parsed["turns"]:
        per_run[t["appRunId"]] = per_run.get(t["appRunId"], 0) + 1
    for r in parsed.get("appRuns") or []:
        r["turns"] = per_run.get(r.get("appRunId"), 0)
    parsed["turnsFromNarration"] = True
    return parsed


def totals(parsed):
    agg = {
        "in": 0,
        "out": 0,
        "total": 0,
        "unattributed": 0,
        "byModel": {},
        "callsWithUsage": 0,
        "callsWithoutUsage": 0,
    }
    for call in parsed.get("llm") or []:
        usage = call.get("tokens")
        if not usage:
            agg["callsWithoutUsage"] += 1
            continue
        agg["callsWithUsage"] += 1
        for key in ("in", "out", "total", "unattributed"):
            if usage.get(key):
                agg[key] += usage[key]
        model = usage.get("model") or call.get("model") or "unknown"
        per = agg["byModel"].setdefault(
            model, {"calls": 0, "in": 0, "out": 0, "total": 0}
        )
        per["calls"] += 1
        for key in ("in", "out", "total"):
            if usage.get(key):
                per[key] += usage[key]
    return agg


# ── splicing the logcat-only network stream into the structured events ─────────


def _runlog_text(msg: str, profile, compiled) -> str:
    """A logcat message reduced to what the structured log would have stored for it."""
    msg = _head_re(profile, compiled).sub("", msg or "", count=1)
    pre = _prefix_re(profile).match(msg)
    if pre and pre.end() > 0:
        msg = msg[pre.end() :]
    return msg.strip()


def _wall_millis(stamp: str, year: int):
    """``MM-DD HH:MM:SS.mmm`` as epoch millis, read as if UTC (one half of a DIFFERENCE)."""
    try:
        month, day = stamp[:5].split("-")
        hour, minute, rest = stamp[6:].split(":")
        second, millis = rest.split(".")
        when = datetime.datetime(
            year,
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
            int(millis) * 1000,
            tzinfo=datetime.timezone.utc,
        )
    except (ValueError, IndexError):
        return None
    return int(when.timestamp() * 1000)


def _logcat_rows(logcat_lines, tag: str) -> list:
    """The (ts, msg, level) of every logcat line carrying the SDK's tag."""
    rows = []
    for raw in _lines_of(logcat_lines):
        m = logcat_line(raw.rstrip("\n"))
        if m and (not tag or m.group("tag").strip() == tag):
            rows.append((m.group("ts"), m.group("msg"), m.group("level")))
    return rows


def _clock_offset(rows, events, year: int, profile, compiled):
    """The modal quarter-hour offset between logcat's wall clock and the event clock.

    Messages that occur exactly once on both sides anchor a delta; ``None`` when no
    message anchors one.
    """
    seen = collections.Counter(
        _runlog_text(e.get("msg") or "", profile, compiled)
        for e in events
        if e.get("ts")
    )
    stamped = {}
    for event in events:
        text = _runlog_text(event.get("msg") or "", profile, compiled)
        if event.get("ts") and seen[text] == 1:
            stamped[text] = event["ts"]
    votes: collections.Counter = collections.Counter()
    for stamp, msg, _level in rows:
        anchor = stamped.get(_runlog_text(msg, profile, compiled))
        wall = _wall_millis(stamp, year)
        if anchor is not None and wall is not None and isinstance(anchor, (int, float)):
            votes[round((wall - anchor) / 900000.0)] += 1
    if not votes:
        return None
    return votes.most_common(1)[0][0] * 900000


def _network_event(anchor: dict, when, level, text: str) -> dict:
    """One spliced network line, placed just after ``anchor`` and inheriting its turn."""
    return {
        "appRunId": anchor["appRunId"],
        "runIndex": anchor["runIndex"],
        "cont": False,
        "seq": (anchor["seq"] or 0) + 0.5,
        "ts": when,
        "monoNanos": None,
        "durationMs": None,
        "level": level,
        "category": None,
        "msg": text,
        "fields": {},
        "sessionId": anchor.get("sessionId"),
        "connectionId": anchor.get("connectionId"),
        "turnId": anchor.get("turnId"),
        "spanId": anchor.get("spanId"),
    }


def merge_logcat_network(events, logcat_lines, profile, compiled) -> int:
    """Splice logcat's network-line stream into the structured events, in place.

    The clocks differ (epoch ms UTC vs a local wall clock with no year or zone), so the
    offset is VOTED: messages that occur exactly once on both sides anchor a delta,
    bucketed to the quarter hour; the modal bucket wins; no anchors, no merge. Each
    network line lands at ``anchor.seq + 0.5`` against the nearest preceding event and
    inherits its turn. Returns the number of lines merged.
    """
    net = _pat(compiled, "net")
    if net is None:
        return 0
    tag = str(getattr(profile, "logcat_tag", "") or "")
    rows = _logcat_rows(logcat_lines, tag)
    if not rows:
        return 0
    year = datetime.datetime.now().year
    offset = _clock_offset(rows, events, year, profile, compiled)
    if offset is None:
        return 0
    ordered = [e for e in events if isinstance(e.get("ts"), (int, float))]
    if not ordered:
        return 0
    ordered.sort(key=lambda e: e["ts"])
    clocks = [e["ts"] for e in ordered]
    merged = 0
    for stamp, msg, level in rows:
        text = _runlog_text(msg, profile, compiled)
        if not net.match(text):
            continue
        wall = _wall_millis(stamp, year)
        if wall is None:
            continue
        when = wall - offset
        index = bisect.bisect_right(clocks, when) - 1
        anchor = ordered[index] if index >= 0 else ordered[0]
        events.append(_network_event(anchor, when, level, text))
        merged += 1
    events.sort(key=lambda e: (e["runIndex"], e["seq"] is None, e["seq"] or 0))
    return merged


# ── the whole report for one capture ───────────────────────────────────────────


_LOGCAT_FALLBACK_GAP = {
    "id": "logcatFallback",
    "what": "seq ordering, monotonic durations, and the turn correlation chain",
    "why": "this capture was parsed from logcat; those fields exist only in "
    "the structured event log, which was not captured.",
}


def _logcat_prompts(logcat, profile, parsed) -> list:
    """The system prompts of a logcat-only capture, each tied to its turn."""
    prompts = [
        {"text": t, "complete": ok, "seq": seq}
        for t, ok, seq in system_prompts(logcat, profile)
    ]
    for p in prompts:
        p["turnId"] = turn_of_seq(parsed, p["seq"])
    return prompts


def _report_counts(parsed: dict, events: list) -> dict:
    """The headline counts of one parsed capture."""
    return {
        "events": len(events),
        "appRuns": len(parsed["appRuns"]),
        "turns": len(parsed["turns"]),
        "llmCalls": len(parsed["llm"]),
        "bindingCalls": len(parsed["bindings"]),
        "toolCalls": len(parsed["tools"]),
        "notes": len(parsed["notes"]),
        "errors": len(parsed["errors"]),
        "narrated": len(parsed["runlog"]),
    }


def _parsed_sections(parsed: dict) -> dict:
    """The parsed streams the report passes through unchanged."""
    keys = (
        "turns",
        "utterances",
        "answers",
        "flowStates",
        "cards",
        "appRuns",
        "llm",
        "bindings",
        "tools",
        "notes",
        "errors",
        "runlog",
        "unresolvedBindings",
        "configs",
    )
    return {key: parsed[key] for key in keys}


def _run_section(source: str, parsed: dict, malformed, manifest, profile) -> dict:
    """The ``run`` block of the report: source, environment claims and traffic hosts."""
    effective_env, hosts = env_from_traffic(parsed["bindings"], profile)
    claimed_env = (manifest or {}).get("env")
    return {
        "source": source,
        "mergedNetworkLines": parsed.get("mergedNetworkLines") or 0,
        "malformedLines": malformed,
        "turnsFromNarration": bool(parsed.get("turnsFromNarration")),
        "appRuns": len(parsed["appRuns"]),
        "env": effective_env or claimed_env,
        "envClaimed": claimed_env,
        "envFromTraffic": effective_env,
        "envDisagrees": bool(
            effective_env and claimed_env and effective_env != claimed_env
        ),
        "hosts": hosts,
        "disabled": parsed.get("disabled") or [],
    }


def build(profile, compiled, *, ndjson=None, logcat=None) -> dict:
    """Parse one capture into the report dict every downstream reader consumes.

    ``ndjson`` and ``logcat`` are each a path or an iterable of lines. With both, the
    structured log is primary and logcat contributes its network stream; with logcat
    alone every line is read through the same grammar with fewer fields resolved. With
    neither, or with input that cannot be read, the result is ``{"error", "content": None}``
    -- this function never raises.
    """
    try:
        if ndjson is None and logcat is None:
            return {
                "error": "no capture: neither a structured log nor a logcat was given",
                "content": None,
            }
        if ndjson is not None:
            source = "ndjson"
            manifests, events, malformed, checkpoints = read_ndjson(ndjson)
            parsed = attribute_turns(parse(events, logcat, profile, compiled))
            prompts = []
        else:
            source = "logcat"
            manifests, events, malformed, checkpoints = read_logcat(logcat, profile)
            parsed = attribute_turns(parse(events, None, profile, compiled))
            prompts = _logcat_prompts(logcat, profile, parsed)
        manifest = manifests[-1] if manifests else None
        gaps = list(GAPS)
        if source == "logcat":
            gaps.append(dict(_LOGCAT_FALLBACK_GAP))
        report = {
            "schema": SCHEMA,
            "run": _run_section(source, parsed, malformed, manifest, profile),
            "manifest": manifest,
            "manifests": manifests,
            "counts": _report_counts(parsed, events),
            "tokens": totals(parsed),
            "systemPrompts": prompts,
            **_parsed_sections(parsed),
            "checkpoints": checkpoints,
            "clock": parsed.get("clock") or {},
            "gaps": gaps,
        }
        return {"error": None, "content": report}
    except Exception as exc:  # noqa: BLE001 - the contract is never to raise
        return {
            "error": "could not parse the capture: " + str(exc)[:200],
            "content": None,
        }
