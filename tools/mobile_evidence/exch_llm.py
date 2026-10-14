"""A model round-trip as an LLM exchange-row dict."""

from __future__ import annotations

import collections
from typing import Any, cast

from tools.mobile_evidence.exch_num import _duration
from tools.mobile_evidence.exch_shape import body_text

_NOT_HTTP_LLM = {
    "requestHeaders": "not applicable \u2014 this row is a model round-trip, not an HTTP call",
    "responseHeaders": "not applicable \u2014 this row is a model round-trip, not an HTTP call",
}


def _llm_outcome(c: dict, tok: dict) -> tuple[str, bool | None]:
    """(badge label, ok) for how a model call ended."""
    err = c.get("error")
    if err:
        return str(err.get("class") or "error"), False
    if c.get("response") is not None:
        return (tok.get("finish") or "answered"), True
    if c.get("orphanUsage"):
        return "usage only", True
    return "never returned", None


def _llm_facts(c: dict, tok: dict, model: str) -> tuple:
    """The (label, value) pairs a model call may show in its query pane."""
    return (
        ("model", model),
        ("requested", tok.get("requested")),
        ("stream", {True: "yes", False: "no"}.get(cast("Any", c.get("stream")))),
        ("messages", c.get("msgs")),
        ("tools offered", c.get("tools")),
        ("stream frames", c.get("frames") or None),
        ("finish reason", tok.get("finish")),
        ("tokens in", tok.get("in")),
        ("tokens out", tok.get("out")),
        ("tokens total", tok.get("total")),
        ("unattributed", tok.get("unattributed")),
    )


def _llm_usage_note(c: dict, tok: dict, meta: collections.OrderedDict) -> None:
    """Add the usage caveats a model call carries (none reported, prose-parsed, orphan)."""
    if not tok:
        meta["usage"] = (
            "none reported \u2014 a stream aborted mid-flight never emits one"
        )
    elif not tok.get("structured"):
        meta["usage source"] = "parsed from the prose line, not the structured event"
    if c.get("orphanUsage"):
        meta["note"] = "usage arrived with no request line to attach it to"


def _llm_meta(c: dict, tok: dict, model: str) -> collections.OrderedDict:
    """The query-pane facts of a model call: what was asked, what it cost."""
    meta: collections.OrderedDict = collections.OrderedDict()
    for key, value in _llm_facts(c, tok, model):
        if value is not None and value != "":
            meta[key] = value
    _llm_usage_note(c, tok, meta)
    return meta


def _prompt_text(c: dict) -> str:
    """The prompt text of a model call, saying why when none was logged."""
    prompts = "\n\n".join(
        filter(None, (body_text(p) for p in (c.get("promptMessages") or [])))
    )
    if not prompts:
        prompts = "[no prompt message was logged for this call \u2014 the app logs prompt bodies only when its body policy allows it]"
    return prompts


def _reply_text(c: dict, err: dict | None) -> str:
    """The reply text of a model call, saying why when empty, with the error appended."""
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
    return reply


def _llm_bodies(c: dict, err: dict | None) -> tuple[str, str]:
    """(prompt text, reply text) of a model call, each saying why when empty."""
    return _prompt_text(c), _reply_text(c, err)


def llm_exchange(c: dict) -> dict:
    """One model round-trip. The prompt is the request, the reply the response, and the badge
    is how the call ENDED -- answered with a finish reason, thrown, or still open."""
    tok = c.get("tokens") or {}
    model = tok.get("model") or c.get("model") or "(unrecorded)"
    label, ok = _llm_outcome(c, tok)
    prompts, reply = _llm_bodies(c, c.get("error"))
    return {
        "notCaptured": dict(_NOT_HTTP_LLM),
        "method": "LLM",
        "path": model,
        "status": None,
        "ok": ok,
        "statusLabel": label,
        "durationMs": _duration(c),
        "query": _llm_meta(c, tok, model),
        "requestBody": prompts,
        "responseBody": reply,
    }
