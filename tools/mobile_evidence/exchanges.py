"""One call -- an endpoint, a model round-trip, a tool -- as the shell's exchange row.

Two halves, both ported from the reference. The ROW SHAPE (:func:`binding_exchange`,
:func:`network_exchange`, :func:`llm_exchange`, :func:`tool_exchange`) turns a parsed
record into the small dict every exchange row reads: method, path, url, status, ok,
statusLabel, durationMs and the query/header/body on each side, with ``notCaptured``
saying per field what the app never logs -- a blank pane would read as "the call
carried none of that" when the truth is "the log never has it". The MARKUP
(:func:`exchange_row`, :func:`seq_exchange`, the ``seq_*`` primitives,
:func:`seqlist`) is the reference shell's vocabulary, so a call reads the same on
this report as on the one it was copied from.

Every string reaches markup through :func:`scrub.e`. There is no lazy text store
here -- the reference parks big bodies in script islands; this report never emits a
script the shell did not ship -- so bodies are inline and capped.

DURATIONS ARE DERIVED, NOT MEASURED. A request and its response are two log
events; the evidence layer stamps the interval as ``derivedMs``, read here in
preference to ``durationMs`` (which the app emits for structured tool events and
rarely fills). A record with neither draws NO bar: a bar of width zero is a
measurement of zero.

This module knows no app: the words on a row come from the record or from the
caller, never from a constant here.
"""

from __future__ import annotations

import collections
import json
import re

from tools.mobile_evidence.scrub import (
    SENSITIVE_KEY_RE,
    e,
    mask_value,
    redact_header,
    scrub_json,
    scrub_text,
)

BODY_LIMIT = 6000

# ── numbers on a row ───────────────────────────────────────────────────────────


def _duration(rec: dict) -> float | None:
    """The call's elapsed time: measured if the app measured it, else derived."""
    got = rec.get("durationMs")
    return got if got is not None else rec.get("derivedMs")


def dur_ms(ms: object) -> str:
    """Sub-second work keeps its milliseconds; a second and over is divided for the reader."""
    if ms is None:
        return ""
    try:
        number = float(ms)
    except (TypeError, ValueError):
        return ""
    return "%d ms" % round(number) if number < 1000 else "%.1fs" % (number / 1000.0)


def fmt_ms(ms: object) -> str:
    if ms is None:
        return "\u2014"
    try:
        number = float(ms)
    except (TypeError, ValueError):
        return "\u2014"
    if number < 60000:
        return "%.1fs" % (number / 1000)
    if number < 3600000:
        return "%.1fm" % (number / 60000)
    return "%.1fh" % (number / 3600000)


# ── bodies ─────────────────────────────────────────────────────────────────────

_JSON_TOK = re.compile(
    r'("(?:\\.|[^"\\])*")\s*:'  # key
    r'|("(?:\\.|[^"\\])*")'  # string
    r"|(-?\d+\.?\d*(?:[eE][+-]?\d+)?)"  # number
    r"|\b(true|false|null)\b"  # literal
)


def _hl_json(text: str) -> str:
    out: list[str] = []
    last = 0
    for match in _JSON_TOK.finditer(text):
        out.append(e(text[last : match.start()]))
        if match.group(1):
            out.append(
                '<span class="jk">'
                + e(match.group(1))
                + '</span><span class="jp">:</span>'
            )
        elif match.group(2):
            out.append('<span class="js">' + e(match.group(2)) + "</span>")
        elif match.group(3):
            out.append('<span class="jn">' + e(match.group(3)) + "</span>")
        else:
            out.append('<span class="jb">' + e(match.group(4)) + "</span>")
        last = match.end()
    out.append(e(text[last:]))
    return "".join(out)


def _sniff(text: str) -> tuple[str, str]:
    """Classify a body so it can be labelled and highlighted. Never guesses silently."""
    stripped = text.strip()
    if not stripped:
        return "empty", ""
    if stripped[0] in "{[":
        try:
            # Scrubbed as a STRUCTURE, before it is ever a string: walking the parsed object
            # is what lets a key be judged by its name while it is still attached to its value.
            return "json", json.dumps(
                scrub_json(json.loads(stripped)), ensure_ascii=False, indent=2
            )
        except (ValueError, TypeError):
            return "text", scrub_text(stripped)
    stripped = scrub_text(stripped)
    if stripped.startswith("<?xml") or (
        stripped.startswith("<") and stripped.endswith(">")
    ):
        return "xml", stripped
    if re.match(r"^\s*(query|mutation|subscription|fragment)\b", stripped):
        return "graphql", stripped
    return "text", stripped


def codebox(text: object, limit: int = BODY_LIMIT) -> str | None:
    """A body as a labelled code block, or None when there is nothing to show.

    JSON is pinned left-to-right (its syntax is), prose takes ``dir="auto"`` so a
    right-to-left body lays out correctly.
    """
    kind, pretty = _sniff("" if text is None else str(text))
    if kind == "empty":
        return None
    clipped = pretty[:limit]
    trunc = (
        ""
        if len(pretty) <= limit
        else '<div class="clip">truncated \u2014 '
        + format(len(pretty) - limit, ",")
        + " more characters</div>"
    )
    if kind == "json":
        pre = '<pre dir="ltr">' + _hl_json(clipped) + "</pre>"
    else:
        pre = '<pre dir="auto">' + e(clipped) + "</pre>"
    return (
        '<div class="code"><div class="codetag">'
        + kind.upper()
        + "</div>"
        + pre
        + trunc
        + "</div>"
    )


def kvtable(pairs: dict | None, mark: bool = False) -> str | None:
    """Key/value pairs as a table, or None when empty. The header mask and both nets apply
    per row: a tool argument row is where an id was once rendered, not a body."""
    if not pairs:
        return None
    rows = []
    for key, value in sorted(pairs.items(), key=lambda item: str(item[0])):
        highlight = (
            ' class="hl"'
            if mark
            and str(key).lower() in {"authorization", "apikey", "accept-language"}
            else ""
        )
        value = redact_header(key, value)
        if SENSITIVE_KEY_RE.search(str(key)):
            value = mask_value(value) if str(value).strip() else value
        else:
            value = scrub_text(str(value))
        blank = not str(value).strip()
        shown = "\u2014" if blank else str(value)
        rows.append(
            "<tr"
            + highlight
            + '><td class="hk">'
            + e(key)
            + '</td><td class="hv'
            + (" blank" if blank else "")
            + '" dir="auto">'
            + e(shown)
            + "</td></tr>"
        )
    return (
        '<div class="tabwrap"><table class="htab"><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


def section(
    title: str, content: str | None, count: int | None = None, note: str = ""
) -> str:
    """One labelled pane section -- collapsible when it has content, inert when it does not.

    An empty section keeps its label (so a pane still reads Query / Headers / Body in the same
    order every time) but carries NO chevron: a disclosure control that toggles nothing lies
    about there being something behind it.
    """
    if content is None:
        return (
            '<div class="sec empty"><h5><span class="chev ghost" aria-hidden="true"></span>'
            + e(title)
            + '<span class="mt">'
            + (e(note) if note else "empty")
            + "</span></h5></div>"
        )
    n = '<span class="cnt">' + str(int(count)) + "</span>" if count else ""
    mt = '<span class="mt">' + e(note) + "</span>" if note else ""
    return (
        '<details class="sec"><summary><span class="chev" aria-hidden="true"></span>'
        + e(title)
        + n
        + mt
        + '</summary><div class="secbody">'
        + content
        + "</div></details>"
    )


def host_of(x: dict) -> str:
    """Origin of one exchange, or '' when the record has no full URL."""
    match = re.match(r"^(https?://[^/]+)", str(x.get("url") or ""))
    return match.group(1) if match else ""


def _ex_bits(x: dict) -> tuple[str, str]:
    """(one-line head, body panes) for one call. ``ok`` may be None -- a call the capture never
    resolved either way -- and folding that into "bad" would invent a failure exactly as
    folding it into "ok" would invent a success. Unknown gets its own tone."""
    query = x.get("query") or {}
    gaps = x.get("notCaptured") or {}
    rq_h = x.get("requestHeaders") or {}
    rq_b = x.get("requestBody") or ""
    rs_h = x.get("responseHeaders") or {}
    rs_b = x.get("responseBody") or ""
    status = x.get("status", -1)
    ok = x["ok"] if "ok" in x else (status is not None and 200 <= status < 300)
    shown = x.get("statusLabel") or (
        status if status not in (-1, None) else "no response"
    )
    scls = "gap" if ok is None else "ok" if ok else "bad"
    verb = str(x.get("method") or "?").upper()
    dur = x.get("durationMs")
    dur_html = (
        '<span class="exdur">' + dur_ms(dur) + "</span>" if dur is not None else ""
    )
    host = host_of(x)
    host_html = '<span class="exhost">' + e(host) + "</span>" if host else ""
    head = (
        '<span class="verb v-' + e(verb.lower()) + '">' + e(verb) + "</span>"
        '<span class="path">'
        + host_html
        + e(x.get("path", ""))
        + "</span>"
        + str(x.get("extra") or "")
        + dur_html
        + '<span class="badge '
        + scls
        + '">'
        + e(str(shown))
        + "</span>"
    )
    inside = str(x.get("inside") or "")
    if not (query or rq_h or str(rq_b).strip() or rs_h or str(rs_b).strip() or inside):
        return head, ""
    body = (
        '<div class="exbody"><div class="pane"><div class="phead"><span class="dot req"></span>Request</div>'
        + section(
            "Query",
            kvtable(query) if isinstance(query, dict) else None,
            len(query) if isinstance(query, dict) else None,
            gaps.get("query", ""),
        )
        + section(
            "Headers",
            kvtable(rq_h, mark=True),
            len(rq_h),
            gaps.get("requestHeaders", ""),
        )
        + section("Body", codebox(rq_b), note=gaps.get("requestBody", ""))
        + '</div><div class="pane"><div class="phead"><span class="dot res"></span>Response <span class="badge '
        + scls
        + ' sm">'
        + e(str(shown))
        + "</span></div>"
        + section("Headers", kvtable(rs_h), len(rs_h), gaps.get("responseHeaders", ""))
        + section("Body", codebox(rs_b), note=gaps.get("responseBody", ""))
        + "</div></div>"
        + inside
    )
    return head, body


def exchange_row(x: dict) -> str:
    """One call, both sides, collapsed to a single scannable line. No disclosure control when
    there is nothing behind it."""
    head, body = _ex_bits(x)
    if not body:
        return (
            '<div class="ex flat"><div class="exhead"><span class="chev off" aria-hidden="true"></span>'
            + head
            + '<span class="mt">nothing captured</span></div></div>'
        )
    return (
        '<details class="ex"><summary><span class="chev" aria-hidden="true"></span>'
        + head
        + "</summary>"
        + body
        + "</details>"
    )


def seq_exchange(x: dict) -> str:
    """The same call as ONE sequence row: the line is the summary, the panes the fold."""
    head, body = _ex_bits(x)
    if not body:
        return head + '<span class="mt">nothing captured</span>'
    return head + SEQ_CUT + body


def exlist(rows: list) -> str:
    return '<div class="exlist">' + "".join(rows) + "</div>"


# ── the row shapes ─────────────────────────────────────────────────────────────


def body_text(shape: dict | None) -> str:
    """A body-policy-shaped field as text -- honestly, because a hash is not a payload."""
    if not shape:
        return ""
    if shape.get("policy") == "NONE":
        return (
            "[not captured \u2014 body policy NONE, "
            + str(shape.get("length"))
            + " chars, hash "
            + str(shape.get("hash"))
            + "]"
        )
    text = str(shape.get("text") or "")
    if shape.get("policy") == "TRUNCATED" and shape.get("omitted"):
        text += (
            "\n\n[+"
            + str(shape["omitted"])
            + " chars omitted by body policy TRUNCATED]"
        )
    return text


def _path_of(url: str) -> str:
    rest = url.split("://", 1)[1] if "://" in url else ""
    return ("/" + rest.split("/", 1)[1]) if "/" in rest else (url if not rest else "/")


def binding_exchange(b: dict) -> dict:
    """One data-layer call. The app logs a success without ever saying WHICH 2xx it was, so
    the badge is labelled rather than invented: "2xx (code not logged)", never a number the
    log did not contain."""
    url = str(b.get("url") or "")
    path = _path_of(url) or str(b.get("target") or "")
    resp = b.get("response") or {}
    body = str(resp.get("text") or "")
    if resp.get("json") is not None:
        body = json.dumps(resp["json"], ensure_ascii=False, indent=1)
    if resp.get("omitted"):
        body += (
            "\n\n[+"
            + str(resp["omitted"])
            + " chars clipped by the app before it was logged]"
        )
    args = b.get("args")
    query = args if isinstance(args, dict) else {}
    req_body = (
        ""
        if isinstance(args, dict)
        else (
            json.dumps(args, ensure_ascii=False)
            if isinstance(args, list)
            else str(args or "")
        )
    )
    extra = '<span class="mt">' + e(b.get("binding") or "") + "</span>"
    if b.get("retried"):
        extra += '<span class="mt">token-refresh retry</span>'
    if b.get("forDependentSuffix"):
        extra += (
            '<span class="mt">dependent \u2026' + e(b["forDependentSuffix"]) + "</span>"
        )
    if b.get("orphanResponse"):
        extra += '<span class="mt">no request logged</span>'
    if b.get("statusKnown"):
        label = None
    elif b.get("ok"):
        label = "2xx (code not logged)"
    elif b.get("ok") is None:
        label = "never returned"
    else:
        label = "failed (code not logged)"
    return {
        "notCaptured": {
            "requestHeaders": "not captured \u2014 the app logs this call as verb, URL and args; the headers it sent never reach the log",
            "responseHeaders": "not captured \u2014 the app logs the response body, never its headers",
        },
        "method": b.get("verb") or "CALL",
        "path": path or str(b.get("binding") or ""),
        "url": url,
        "status": b.get("status") if b.get("statusKnown") else -1,
        "ok": b.get("ok"),
        "statusLabel": label,
        "durationMs": _duration(b),
        "query": query,
        "requestBody": req_body,
        "responseBody": body,
        "extra": extra,
    }


def network_exchange(rec: dict) -> dict:
    """One network-log line as an endpoint row. THREE STATES, because the log has three: the
    outcome bracket is optional, so ``ok`` is None whenever the app wrote the line without
    one -- a call nobody proved succeeded is not a call that succeeded."""
    url = str(rec.get("url") or "")
    path, _, query = _path_of(url).partition("?")
    outcome = str(rec.get("outcome") or "").upper()
    ok = True if outcome == "SUCCESS" else False if outcome == "ERROR" else None
    label = (
        "2xx (code not logged)"
        if ok
        else "failed (code not logged)"
        if ok is False
        else "outcome not logged"
    )
    q: dict = {}
    for part in query.split("&") if query else ():
        key, _, value = part.partition("=")
        if key:
            q[key] = value
    return {
        "notCaptured": {
            "requestHeaders": "not captured \u2014 the app logs this call as one line: verb, URL and outcome. No header reaches the log",
            "responseHeaders": "not captured \u2014 the same line carries no response headers",
            "requestBody": "not captured \u2014 the app logs no request body on this line",
            "responseBody": "not captured \u2014 the network line carries no body; a data-layer row is where a body appears, and this call has none",
        },
        "method": rec.get("verb") or "CALL",
        "path": path or url,
        "url": url,
        "status": -1,
        "ok": ok,
        "statusLabel": label,
        "query": q,
        "requestBody": "",
        "responseBody": "",
        "extra": '<span class="mt">network log only \u2014 no data-layer row</span>',
    }


def llm_exchange(c: dict) -> dict:
    """One model round-trip. The prompt is the request, the reply the response, and the badge
    is how the call ENDED -- answered with a finish reason, thrown, or still open."""
    tok = c.get("tokens") or {}
    model = tok.get("model") or c.get("model") or "(unrecorded)"
    err = c.get("error")
    if err:
        label, ok = str(err.get("class") or "error"), False
    elif c.get("response") is not None:
        label, ok = (tok.get("finish") or "answered"), True
    elif c.get("orphanUsage"):
        label, ok = "usage only", True
    else:
        label, ok = "never returned", None
    meta: collections.OrderedDict = collections.OrderedDict()
    for key, value in (
        ("model", model),
        ("requested", tok.get("requested")),
        ("stream", {True: "yes", False: "no"}.get(c.get("stream"))),
        ("messages", c.get("msgs")),
        ("tools offered", c.get("tools")),
        ("stream frames", c.get("frames") or None),
        ("finish reason", tok.get("finish")),
        ("tokens in", tok.get("in")),
        ("tokens out", tok.get("out")),
        ("tokens total", tok.get("total")),
        ("unattributed", tok.get("unattributed")),
    ):
        if value is not None and value != "":
            meta[key] = value
    if not tok:
        meta["usage"] = (
            "none reported \u2014 a stream aborted mid-flight never emits one"
        )
    elif not tok.get("structured"):
        meta["usage source"] = "parsed from the prose line, not the structured event"
    if c.get("orphanUsage"):
        meta["note"] = "usage arrived with no request line to attach it to"
    prompts = "\n\n".join(
        filter(None, (body_text(p) for p in (c.get("promptMessages") or [])))
    )
    if not prompts:
        prompts = "[no prompt message was logged for this call \u2014 the app logs prompt bodies only when its body policy allows it]"
    reply = body_text(c.get("response"))
    if not reply and not err:
        reply = (
            "[no reply body was logged for this call]"
            if c.get("response") is not None
            else "[this call logged no reply at all]"
        )
    if err:
        reply = (
            (reply + "\n\n" if reply else "")
            + str(err.get("class") or "error")
            + ": "
            + str(err.get("message") or "")
        )
    return {
        "notCaptured": {
            "requestHeaders": "not applicable \u2014 this row is a model round-trip, not an HTTP call",
            "responseHeaders": "not applicable \u2014 this row is a model round-trip, not an HTTP call",
        },
        "method": "LLM",
        "path": model,
        "status": None,
        "ok": ok,
        "statusLabel": label,
        "durationMs": _duration(c),
        "query": meta,
        "requestBody": prompts,
        "responseBody": reply,
    }


def tool_exchange(t: dict) -> dict:
    """One tool call: what the model asked for, and what came back -- with the reason when
    nothing did."""
    kind = " \u00b7 " + str(t["kind"]) if t.get("kind") else ""
    result = t.get("resultText")
    if result:
        response, gap = str(result), ""
    elif t.get("status") == "done":
        response, gap = (
            "",
            "not captured \u2014 the app logged this tool as finished but wrote no result text with it",
        )
    else:
        response, gap = (
            "",
            "the tool never reported back \u2014 there is no completion line for this invocation in the capture",
        )
    return {
        "notCaptured": {
            "requestHeaders": "not applicable \u2014 this row is a tool invocation, not an HTTP call",
            "responseHeaders": "not applicable \u2014 this row is a tool invocation, not an HTTP call",
            "query": "not applicable \u2014 a tool call has arguments, shown as the request body, not a query string",
            "responseBody": gap,
        },
        "method": "TOOL",
        "path": str(t.get("tool") or "(unnamed)") + kind,
        "status": None,
        "ok": True
        if t.get("status") in ("called", "done")
        else (False if t.get("status") else None),
        "statusLabel": t.get("status") or "unknown",
        "durationMs": _duration(t),
        "requestBody": body_text(t.get("args"))
        + (
            "\n\n[arguments recovered from the model's own reply: the app logs the call with empty args]"
            if t.get("argsRecovered")
            else ""
        ),
        "responseBody": response,
    }


# ── the run sequence ───────────────────────────────────────────────────────────
#
# One clock-ordered stream per record. Stable within a tie by rank: a turn opens before the
# model is asked, the model before the tool it proposes, the tool before the requests it
# causes. Ties are common -- a tool and its first request often land on the same millisecond.

SEQ_RANK = {
    "turn": -1,
    "user": 0,
    "tap": 0,
    "prompt": 1,
    "llm": 2,
    "event": 3,
    "call": 3,
    "done": 3,
    "cap": 4,
    "api": 5,
    "net": 5,
    "auth": 5,
    "app": 5,
    "voice": 5,
    "step": 6,
    "flow": 7,
    "card": 8,
    "log": 9,
    "reply": 10,
    "agent": 11,
}

SEQ_CUT = "<!--seq-cut-->"


def seq_row(kind: str, clock: str, inner: str, bar: str = "") -> str:
    """One row of the sequence: chevron slot, clock, kind, one line, then its own pills and
    bar -- and the WHOLE line is the click target. A builder with more to show appends
    ``SEQ_CUT`` and the detail after it; a row with nothing behind it draws a ghost chevron in
    the same slot so lines start at the same x."""
    head, _cut, detail = inner.partition(SEQ_CUT)
    classes = "seq is-" + e(kind)
    if kind == "reply":
        classes += (
            " is-sara"  # the shell colours the app's reply row under this class name
        )
    if not detail.strip():
        return (
            '<li class="'
            + classes
            + '"><span class="chev ghost" aria-hidden="true"></span>'
            '<span class="seqt">'
            + e(clock)
            + '</span><div class="seqmain">'
            + head
            + "</div>"
            + bar
            + "</li>"
        )
    return (
        '<li class="'
        + classes
        + '"><details class="seqfold"><summary><span class="chev" aria-hidden="true"></span>'
        '<span class="seqt">'
        + e(clock)
        + '</span><div class="seqmain">'
        + head
        + "</div>"
        + bar
        + '</summary><div class="seqdetail">'
        + detail
        + "</div></details></li>"
    )


def seq_session(
    note: str, pill_html: str = "", extra: str = "", label: str | None = "session"
) -> str:
    return seq_row(
        "start",
        "0.0s",
        ('<span class="seqk">' + e(label) + "</span>" if label else "")
        + ('<span class="mt">' + note + "</span>" if note else "")
        + pill_html
        + ((SEQ_CUT + extra) if extra else ""),
    )


def seq_user(text: str, pill_html: str = "") -> str:
    """The conversation is a render path too: scrubbed HERE, at the primitive."""
    text = scrub_text(text or "")
    full = (
        (SEQ_CUT + '<p class="seqfull" dir="auto">' + e(text) + "</p>")
        if len(text) > 160
        else ""
    )
    return (
        '<span class="seqk">user</span><span class="seqtx" dir="auto">'
        + e(text)
        + "</span>"
        + pill_html
        + full
    )


def seq_reply(said: str, extra: str = "") -> str:
    """The app's reply. *said* arrives already escaped (or as an em-dash marker); *extra* is
    any block the caller attaches, and it belongs in the fold with a long reply's full text."""
    said = scrub_text(said or "")
    extra = scrub_text(extra or "")
    detail = (
        '<p class="seqfull" dir="auto">' + said + "</p>" if len(said) > 160 else ""
    ) + (extra or "")
    return (
        '<span class="seqk">reply</span><span class="seqtx" dir="auto">'
        + said
        + "</span>"
        + ((SEQ_CUT + detail) if detail else "")
    )


def seq_prompt(
    chars: int, body: str, note: str = "", head: str = "", meta: str = ""
) -> str:
    mt = '<span class="mt">' + e(note) + "</span>" if note else ""
    ex = '<span class="mt">' + e(meta) + "</span>" if meta else ""
    return (
        '<span class="seqk">prompt</span><span class="seqtx">'
        + e(head or "what the model was given")
        + '</span><span class="mt">'
        + format(int(chars), ",")
        + " ch</span>"
        + ex
        + mt
        + SEQ_CUT
        + '<div class="pane">'
        + body
        + "</div>"
    )


def seq_tool(name: str, args: str = "", note: str = "", body: str = "") -> str:
    return (
        '<span class="seqk">tool</span><span class="tlname">'
        + e(name)
        + "</span>"
        + ('<span class="mt">' + e(note) + "</span>" if note else "")
        + args
        + ((SEQ_CUT + body) if body else "")
    )


def seq_step(label: str, value: object) -> str:
    """What the DRIVER did -- typed, sent, waited -- in the same stream as what the app did."""
    return (
        '<span class="seqk">'
        + e(label)
        + '</span><span class="seqtx" dir="auto">'
        + e(str(value))
        + "</span>"
    )


def seq_flow(head: str, bits: str = "", body: str = "", pill_html: str = "") -> str:
    detail = (bits or "") + (body or "")
    return (
        '<span class="seqk">flow</span><span class="seqtx" dir="auto">'
        + e(head)
        + "</span>"
        + pill_html
        + ((SEQ_CUT + detail) if detail else "")
    )


def seq_cap(
    cid: str,
    text: str = "",
    prefill: str = "",
    started: bool = True,
    pill_html: str = "",
) -> str:
    seed = (
        '<span class="inseed" dir="auto">' + e(prefill) + "</span>" if prefill else ""
    )
    desc = '<span class="capdesc" dir="auto">' + e(text) + "</span>" if text else ""
    detail = desc + seed
    return (
        '<span class="seqk">capability</span><span class="capid">'
        + e(cid)
        + "</span>"
        + ('<span class="tn">started</span>' if started else "")
        + pill_html
        + ((SEQ_CUT + detail) if detail else "")
    )


def seq_card(what: str, replaced: bool = False) -> str:
    return (
        '<span class="seqk">card</span><span class="seqtx">'
        + e(what)
        + (" (replaced the one before it)" if replaced else "")
        + "</span>"
    )


def seq_note(text: str) -> str:
    return (
        '<span class="seqk">note</span><span class="seqtx" dir="auto">'
        + e(text)
        + "</span>"
    )


def seq_turn(n: int, said: str = "") -> str:
    return (
        '<span class="seqk">turn '
        + str(int(n))
        + "</span>"
        + ('<span class="seqtx" dir="auto">' + e(said) + "</span>" if said else "")
    )


def seq_log(
    lane: str,
    head: str,
    body: str = "",
    bad: bool = False,
    times: int = 1,
    pill_html: str = "",
) -> str:
    """A line the app narrated that no dedicated row shape claims. Deliberately UNPARSED."""
    rep = (
        '<span class="seqx">&times;' + str(int(times)) + "</span>" if times > 1 else ""
    )
    more = (SEQ_CUT + '<pre dir="auto">' + e(body) + "</pre>") if body else ""
    return (
        '<span class="seqk">'
        + e(lane)
        + '</span><span class="seqtx mono'
        + (" bad" if bad else "")
        + '" dir="auto">'
        + e(head)
        + "</span>"
        + rep
        + pill_html
        + more
    )


def ms_clock(key: float | None, first: float | None) -> str:
    """Relative to the first recorded thing: the absolute epoch is noise, the GAP is not."""
    if key is None or first is None:
        return ""
    rel = key - first
    return ("+%.1fs" % (rel / 1000.0)) if rel else "0.0s"


def no_clock(key: object, first: object) -> str:
    """For records that carry an ORDER but no clock: printing +0.0s would invent a measurement."""
    return ""


def seqlist(items: list, session: str | None = None, clock=ms_clock) -> str:
    """Merge everything witnessed into one ordered stream.

    *items* is ``[(key, rank, kind, inner_html[, clock_label[, bar_html]])]``; *key* sorts
    and *rank* breaks ties inside it. A fifth element overrides the clock for that row alone,
    a sixth is its waterfall bar; both optional, for the same reason: a report that timed
    nothing passes none and draws none.
    """
    items = [item for item in items if item[3]]
    if not items:
        return ""
    items.sort(key=lambda item: (item[0], item[1]))
    first = items[0][0]
    rows = [session] if session else []
    for item in items:
        key, _rank, kind, inner = item[:4]
        label = item[4] if len(item) > 4 and item[4] is not None else clock(key, first)
        bar = item[5] if len(item) > 5 and item[5] else ""
        rows.append(seq_row(kind, label, inner, bar))
    return '<ul class="seqlist">' + "".join(rows) + "</ul>"
