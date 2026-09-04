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
    except (TypeError, ValueError):
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
        events.append(
            {
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
        )
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
    structured = getattr(profile, "structured", None) or {}
    tool_cat = structured.get("tool_category")
    cost_cat = structured.get("cost_category")
    config_cat = structured.get("config_category")
    tool_msgs = structured.get("tool_msgs") or []
    cost_msgs = structured.get("cost_msgs") or []
    config_msgs = structured.get("config_msg") or []
    bad_marks = _names(getattr(profile, "runlog_bad", None) or [])
    head_re = _head_re(profile, compiled)

    bindings, llm, tools, notes, errors, runlog = [], [], [], [], [], []
    open_binding: dict = {}
    recovered = []
    utterances, answers, invocations, flow_states, cards, configs = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    open_llm = None
    last_usage = None
    turns: dict = {}
    runs: dict = {}
    clock: dict = {}
    run_index = object()

    for ev in events:
        if ev.get("runIndex") != run_index:
            run_index = ev.get("runIndex")
            open_binding, open_llm, last_usage = {}, None, None
        run = ev.get("appRunId") or (
            "run-%s" % run_index if run_index is not None else None
        )
        seq = ev.get("seq")
        ts = ev.get("ts")
        if run:
            r = runs.setdefault(
                run,
                {
                    "appRunId": run,
                    "runIndex": run_index,
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
            if (
                isinstance(ts, (int, float, str))
                and not isinstance(ts, bool)
                and ts != ""
            ):
                try:
                    if r["firstTs"] is None or ts < r["firstTs"]:
                        r["firstTs"] = ts
                    if r["lastTs"] is None or ts > r["lastTs"]:
                        r["lastTs"] = ts
                except TypeError:
                    pass
                clock.setdefault(run, {})[str(seq)] = ts

        msg = ev.get("msg") or ""
        fields = ev.get("fields") or {}
        cat = ev.get("category")
        turn = ev.get("turnId")
        if turn:
            t = turns.setdefault(
                turn,
                {
                    "turnId": turn,
                    "connectionId": ev.get("connectionId"),
                    "appRunId": run,
                    "firstSeq": seq,
                    "lastSeq": seq,
                    "llm": 0,
                    "bindings": 0,
                    "tools": 0,
                    "errors": 0,
                },
            )
            t["lastSeq"] = seq

        def bump(key):
            if turn:
                turns[turn][key] += 1
            if run:
                runs[run][key] += 1

        # -- structured events first: they carry fields the prose cannot --
        if tool_cat and cat == tool_cat and _msg_in(msg, tool_msgs):
            entry = {
                "seq": seq,
                "turnId": turn,
                "appRunId": run,
                "tool": fields.get("tool"),
                "status": msg.split(".", 1)[1] if "." in msg else msg,
                "kind": fields.get("kind"),
                "args": _body_shape(fields.get("body.args")),
                "durationMs": ev.get("durationMs"),
            }
            tools.append(entry)
            bump("tools")
            if entry["status"] == "failed":
                errors.append({"seq": seq, "kind": "tool", "detail": entry})
                bump("errors")
            continue

        if cost_cat and cat == cost_cat and _msg_in(msg, cost_msgs):
            usage = {
                "seq": seq,
                "turnId": turn,
                "appRunId": run,
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
            if open_llm is not None and llm[open_llm].get("tokens") is None:
                llm[open_llm]["tokens"] = usage
                llm[open_llm]["endSeq"] = seq
                last_usage = llm[open_llm]["tokens"]
            elif _same_usage(last_usage, usage):
                # The same round-trip, reported twice (prose then structured): the
                # structured form replaces the prose in place, never appends.
                last_usage.update(usage)
            else:
                llm.append(
                    {
                        "seq": seq,
                        "turnId": turn,
                        "appRunId": run,
                        "model": usage["model"],
                        "tokens": usage,
                        "promptMessages": [],
                        "orphanUsage": True,
                    }
                )
                last_usage = llm[-1]["tokens"]
            open_llm = None
            continue

        if config_cat and cat == config_cat and _msg_in(msg, config_msgs):
            configs.append(
                {"seq": seq, "turnId": turn, "appRunId": run, "fields": dict(fields)}
            )
            continue

        # -- narrated prose, by stream --
        m = _match(compiled, "bind_req", msg)
        if m:
            g = m.groupdict()
            bindings.append(
                {
                    "seq": seq,
                    "turnId": turn,
                    "appRunId": run,
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
            open_binding.setdefault(g.get("name"), []).append(len(bindings) - 1)
            bump("bindings")
            continue

        m = _match(compiled, "call_req", msg)
        if m:
            g = m.groupdict()
            target = (g.get("target") or "").strip()
            is_url = target.startswith(("http://", "https://"))
            bindings.append(
                {
                    "seq": seq,
                    "turnId": turn,
                    "appRunId": run,
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
            open_binding.setdefault("sh:" + str(g.get("name")), []).append(
                len(bindings) - 1
            )
            bump("bindings")
            continue

        m = _match(compiled, "call_res", msg)
        if m:
            g = m.groupdict()
            idx = _take_open(open_binding, "sh:" + str(g.get("name")))
            status, body = g.get("status"), g.get("body")
            summary = (g.get("summary") or "").strip()
            entry = {
                "text": body if body else summary,
                "omitted": 0,
                "json": _maybe_json(body) if body else None,
            }
            if idx is not None:
                bindings[idx].update(
                    {
                        "ok": True,
                        "status": int(status) if status else None,
                        "statusKnown": bool(status),
                        "summary": summary,
                        "response": entry,
                        "endSeq": seq,
                    }
                )
                continue
            # An unpaired shorthand response is NOT a call: it falls through to the
            # narrated lane it has always been in.

        m = _match(compiled, "bind_res", msg)
        if m:
            g = m.groupdict()
            idx = _take_open(open_binding, g.get("name"))
            body = g.get("body") or ""
            clipped = RE_TRUNC_TAIL.search(body)
            entry = {
                "text": RE_TRUNC_TAIL.sub("", body) if clipped else body,
                "omitted": int(clipped.group(1)) if clipped else 0,
                "json": _maybe_json(RE_TRUNC_TAIL.sub("", body)),
            }
            target = (
                bindings[idx]
                if idx is not None
                else {
                    "seq": seq,
                    "turnId": turn,
                    "appRunId": run,
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
                    "endSeq": seq,
                }
            )
            if idx is None:
                bindings.append(target)
                bump("bindings")
            continue

        m = _match(compiled, "bind_err", msg)
        if m:
            g = m.groupdict()
            idx = _take_open(open_binding, g.get("name"))
            detail = {
                "ok": False,
                "status": _int_or_none(g.get("status")),
                "statusKnown": g.get("status") is not None,
                "endSeq": seq,
                "response": {
                    "text": g.get("body"),
                    "omitted": None,
                    "json": _maybe_json(g.get("body")),
                },
            }
            if idx is not None:
                bindings[idx].update(detail)
                errors.append({"seq": seq, "kind": "binding", "detail": bindings[idx]})
            else:
                orphan = {
                    "seq": seq,
                    "turnId": turn,
                    "appRunId": run,
                    "binding": g.get("name"),
                    "verb": None,
                    "url": None,
                    "args": None,
                    "retried": False,
                    "orphanResponse": True,
                }
                orphan.update(detail)
                bindings.append(orphan)
                errors.append({"seq": seq, "kind": "binding", "detail": orphan})
                bump("bindings")
            bump("errors")
            continue

        m = _match(compiled, "bind_retry", msg)
        if m:
            queue = open_binding.get(m.groupdict().get("name"))
            if queue:
                bindings[queue[0]]["retried"] = True
            continue

        m = _match(compiled, "llm_req", msg)
        if m:
            g = m.groupdict()
            llm.append(
                {
                    "seq": seq,
                    "turnId": turn,
                    "appRunId": run,
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
            open_llm = len(llm) - 1
            # Prompt messages on the tail of the same record (structured path); a body
            # that spans lines is appended to the message before it.
            for _line in msg[m.end() :].split("\n"):
                _p = _match(compiled, "prompt_msg", _line)
                if _p:
                    llm[open_llm]["promptMessages"].append(
                        _body_shape(_p.group("body"))
                    )
                elif llm[open_llm]["promptMessages"] and _line.strip():
                    _prev = llm[open_llm]["promptMessages"][-1]
                    if isinstance(_prev, dict) and isinstance(_prev.get("text"), str):
                        _prev["text"] += "\n" + _line
            bump("llm")
            continue

        m = _match(compiled, "prompt_msg", msg)
        if m and open_llm is not None:
            llm[open_llm]["promptMessages"].append(_body_shape(m.group("body")))
            continue

        m = _match(compiled, "llm_frame", msg)
        if m and open_llm is not None:
            llm[open_llm]["frames"] += 1
            continue

        m = _match(compiled, "llm_res", msg)
        if m:
            body = m.groupdict().get("body") or ""
            if open_llm is not None:
                llm[open_llm]["response"] = _body_shape(body)
                llm[open_llm]["endSeq"] = seq
            tool_call = _pat(compiled, "tool_call")
            if tool_call is not None:
                for call in tool_call.finditer(body):
                    recovered.append(
                        {
                            "seq": seq,
                            "turnId": turn,
                            "appRunId": run,
                            "tool": call.group("tool"),
                            "status": "called",
                            "kind": None,
                            "args": _body_shape(call.group("args")),
                            "durationMs": None,
                            "argsRecovered": True,
                        }
                    )
            continue

        m = _match(compiled, "llm_err", msg)
        if m:
            g = m.groupdict()
            err = {"class": g.get("cls"), "message": g.get("msg")}
            if open_llm is not None and llm[open_llm].get("response") is None:
                llm[open_llm]["error"] = err
                llm[open_llm]["endSeq"] = seq
                open_llm = None
            errors.append(
                {
                    "seq": seq,
                    "kind": "llm",
                    "turnId": turn,
                    "appRunId": run,
                    "detail": err,
                }
            )
            bump("errors")
            continue

        m = _match(compiled, "usage", msg)
        if m:
            g = m.groupdict()
            usage = {
                "seq": seq,
                "turnId": turn,
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
            if open_llm is not None and llm[open_llm].get("tokens") is None:
                llm[open_llm]["tokens"] = usage
                llm[open_llm]["endSeq"] = seq
                last_usage = llm[open_llm]["tokens"]
            elif _same_usage(last_usage, usage):
                last_usage.update(usage)
            else:
                llm.append(
                    {
                        "seq": seq,
                        "turnId": turn,
                        "appRunId": run,
                        "model": usage["model"],
                        "tokens": usage,
                        "promptMessages": [],
                        "orphanUsage": True,
                    }
                )
                last_usage = llm[-1]["tokens"]
            continue

        m = _match(compiled, "note", msg)
        if m:
            g = m.groupdict()
            notes.append(
                {
                    "seq": seq,
                    "turnId": turn,
                    "appRunId": run,
                    "noteId": g.get("id"),
                    "text": g.get("text"),
                    "lang": "rtl" if _has_rtl(msg) else "ltr",
                }
            )
            continue

        m = _match(compiled, "agent_turn", msg)
        if m:
            g = m.groupdict()
            utterances.append(
                {
                    "seq": seq,
                    "turnId": turn,
                    "appRunId": run,
                    "text": g.get("text"),
                    "history": _int_or_none(g.get("history")),
                }
            )
            continue

        m = _match(compiled, "agent_answer", msg)
        if m:
            answers.append(
                {
                    "seq": seq,
                    "turnId": turn,
                    "appRunId": run,
                    "text": m.groupdict().get("text"),
                }
            )
            continue

        m = _match(compiled, "tool_invoke", msg)
        if m:
            g = m.groupdict()
            invocations.append(
                {
                    "seq": seq,
                    "turnId": turn,
                    "appRunId": run,
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
            continue

        m = _match(compiled, "tool_done", msg)
        if m:
            g = m.groupdict()
            for entry in reversed(invocations):
                if entry["tool"] == g.get("tool") and entry["resultText"] is None:
                    entry["resultText"] = g.get("result")
                    entry["status"] = "done"
                    entry["endSeq"] = seq
                    break
            continue

        m = _match(compiled, "flow_state", msg)
        if m:
            g = m.groupdict()
            rest = g.get("rest") or ""
            head, _, note = rest.partition("note=")
            field_pat = _pat(compiled, "flow_field")
            state = dict(field_pat.findall(head)) if field_pat is not None else {}
            state.update(
                {
                    "seq": seq,
                    "turnId": turn,
                    "appRunId": run,
                    "tool": g.get("tool"),
                    "note": note.strip() or None,
                }
            )
            flow_states.append(state)
            continue

        m = _match(compiled, "card_push", msg)
        if m:
            g = m.groupdict()
            cards.append(
                {
                    "seq": seq,
                    "turnId": turn,
                    "appRunId": run,
                    "kind": g.get("kind"),
                    "replaced": g.get("replaced") == "true",
                }
            )
            continue

        m = _match(compiled, "agent_fail", msg)
        if m:
            errors.append(
                {
                    "seq": seq,
                    "kind": "agent",
                    "turnId": turn,
                    "appRunId": run,
                    "detail": {"message": (m.groupdict().get("msg") or "").strip()},
                }
            )
            bump("errors")
        elif ev.get("level") in ("E", "ERROR"):
            errors.append(
                {
                    "seq": seq,
                    "kind": "log",
                    "turnId": turn,
                    "appRunId": run,
                    "detail": {"category": cat, "message": msg},
                }
            )
            bump("errors")

        # The fall-through stream: every narrated line no matcher claimed, kept whole.
        if ev.get("cont"):
            continue
        head, _sep, rest = msg.partition("\n")
        head = head_re.sub("", head, count=1).strip()
        if not head:
            continue
        net = _match(compiled, "net", head)
        ng = net.groupdict() if net else {}
        runlog.append(
            {
                "seq": seq,
                "turnId": turn,
                "appRunId": run,
                "lane": runlog_lane(head, profile, compiled),
                "head": head,
                "body": rest.strip() or None,
                "verb": ng.get("verb"),
                "url": ng.get("url"),
                "outcome": ng.get("outcome"),
                "bad": any(b in head for b in bad_marks),
                "category": cat,
                "level": ev.get("level"),
            }
        )

    unresolved = [bindings[i]["binding"] for q in open_binding.values() for i in q]
    # One tool call from two half-witnesses: the invocation line carries the outcome,
    # the model's own reply carries the real arguments.
    if invocations:
        pool = list(recovered)
        for entry in invocations:
            for i, cand in enumerate(pool):
                if cand["tool"] == entry["tool"]:
                    entry["args"] = cand["args"]
                    entry["argsRecovered"] = True
                    pool.pop(i)
                    break
        if not tools:
            tools = invocations
    elif not tools and recovered:
        tools = recovered
    for t in tools:
        if t.get("turnId") and t["turnId"] in turns:
            turns[t["turnId"]]["tools"] += 1
        if t.get("appRunId") and t["appRunId"] in runs:
            runs[t["appRunId"]]["tools"] += 1
    for t in turns.values():
        if t.get("appRunId") and t["appRunId"] in runs:
            runs[t["appRunId"]]["turns"] += 1

    disabled = _disabled_pairs(compiled)
    for name in STREAMS:
        if _pat(compiled, name) is None and not any(d[0] == name for d in disabled):
            disabled.append((name, "no pattern in the profile"))
    return {
        "clock": clock,
        "bindings": bindings,
        "llm": llm,
        "tools": tools,
        "notes": notes,
        "errors": errors,
        "runlog": runlog,
        "utterances": utterances,
        "answers": answers,
        "flowStates": flow_states,
        "cards": cards,
        "configs": configs,
        "turns": [
            turns[k] for k in sorted(turns, key=lambda k: turns[k]["firstSeq"] or 0)
        ],
        "appRuns": sorted(runs.values(), key=lambda r: r["runIndex"]),
        "unresolvedBindings": unresolved,
        "mergedNetworkLines": merged,
        "disabled": disabled,
    }


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

    def turn_of(run, seq):
        if seq is None:
            return None
        for start, end, tid in bounds_by_run.get(run) or ():
            if seq >= start and (end is None or seq <= end):
                return tid
        return None

    def run_of(x):
        return x.get("appRunId") or (x.get("detail") or {}).get("appRunId")

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
            tid = turn_of(run_of(x), x.get("seq"))
            x["turnId"] = tid
            if tid:
                t = turns[tid]
                t["lastSeq"] = max(t["lastSeq"], x.get("seq") or 0)
                if key in counted:
                    t[counted[key]] += 1
    for utts in by_run.values():
        for u in utts:
            u["turnId"] = turn_of(u.get("appRunId"), u.get("seq"))
    for err in parsed.get("errors") or []:
        tid = turn_of(run_of(err), err.get("seq"))
        err["turnId"] = tid
        if tid:
            turns[tid]["errors"] += 1
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
    rows = []
    for raw in _lines_of(logcat_lines):
        m = logcat_line(raw.rstrip("\n"))
        if m and (not tag or m.group("tag").strip() == tag):
            rows.append((m.group("ts"), m.group("msg"), m.group("level")))
    if not rows:
        return 0
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
    year = datetime.datetime.now().year
    votes: collections.Counter = collections.Counter()
    for stamp, msg, _level in rows:
        anchor = stamped.get(_runlog_text(msg, profile, compiled))
        wall = _wall_millis(stamp, year)
        if anchor is not None and wall is not None and isinstance(anchor, (int, float)):
            votes[round((wall - anchor) / 900000.0)] += 1
    if not votes:
        return 0
    offset = votes.most_common(1)[0][0] * 900000
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
        events.append(
            {
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
        )
        merged += 1
    events.sort(key=lambda e: (e["runIndex"], e["seq"] is None, e["seq"] or 0))
    return merged


# ── the whole report for one capture ───────────────────────────────────────────


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
            prompts = [
                {"text": t, "complete": ok, "seq": seq}
                for t, ok, seq in system_prompts(logcat, profile)
            ]
            for p in prompts:
                p["turnId"] = turn_of_seq(parsed, p["seq"])
        manifest = manifests[-1] if manifests else None
        effective_env, hosts = env_from_traffic(parsed["bindings"], profile)
        claimed_env = (manifest or {}).get("env")
        gaps = list(GAPS)
        if source == "logcat":
            gaps.append(
                {
                    "id": "logcatFallback",
                    "what": "seq ordering, monotonic durations, and the turn correlation chain",
                    "why": "this capture was parsed from logcat; those fields exist only in "
                    "the structured event log, which was not captured.",
                }
            )
        report = {
            "schema": SCHEMA,
            "run": {
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
            },
            "manifest": manifest,
            "manifests": manifests,
            "counts": {
                "events": len(events),
                "appRuns": len(parsed["appRuns"]),
                "turns": len(parsed["turns"]),
                "llmCalls": len(parsed["llm"]),
                "bindingCalls": len(parsed["bindings"]),
                "toolCalls": len(parsed["tools"]),
                "notes": len(parsed["notes"]),
                "errors": len(parsed["errors"]),
                "narrated": len(parsed["runlog"]),
            },
            "tokens": totals(parsed),
            "systemPrompts": prompts,
            "turns": parsed["turns"],
            "utterances": parsed["utterances"],
            "answers": parsed["answers"],
            "flowStates": parsed["flowStates"],
            "cards": parsed["cards"],
            "appRuns": parsed["appRuns"],
            "llm": parsed["llm"],
            "bindings": parsed["bindings"],
            "tools": parsed["tools"],
            "notes": parsed["notes"],
            "errors": parsed["errors"],
            "runlog": parsed["runlog"],
            "unresolvedBindings": parsed["unresolvedBindings"],
            "configs": parsed["configs"],
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
