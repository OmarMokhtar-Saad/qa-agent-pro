"""The shorthand call arms: request and response of one ``name(args)`` call."""

from __future__ import annotations

from tools.mobile_evidence.grammar_state import (
    _bump,
    _Line,
    _ParseState,
    _stamp,
    _take_open,
)
from tools.mobile_evidence.grammar_values import _maybe_json


def _call_entry(line: _Line, g: dict) -> dict:
    """The record of a shorthand call that has just been requested."""
    target = (g.get("target") or "").strip()
    is_url = target.startswith(("http://", "https://"))
    return {
        **_stamp(line),
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


def _on_call_req(state: _ParseState, line: _Line, m) -> bool:
    """Open a shorthand call from a ``call_req`` match; always True."""
    g = m.groupdict()
    state.bindings.append(_call_entry(line, g))
    state.open_binding.setdefault("sh:" + str(g.get("name")), []).append(
        len(state.bindings) - 1
    )
    _bump(state, line, "bindings")
    return True


def _summary(g: dict) -> str:
    return (g.get("summary") or "").strip()


def _call_body(g: dict) -> dict:
    """The response of a ``call_res`` match: its body, else its summary."""
    body = g.get("body")
    return {
        "text": body if body else _summary(g),
        "omitted": 0,
        "json": _maybe_json(body) if body else None,
    }


def _call_closing(g: dict, body: dict) -> dict:
    """The fields that close a shorthand call."""
    status = g.get("status")
    return {
        "ok": True,
        "status": int(status) if status else None,
        "statusKnown": bool(status),
        "summary": _summary(g),
        "response": body,
    }


def _on_call_res(state: _ParseState, line: _Line, m) -> bool:
    """Close the open shorthand call of a ``call_res`` match in place.

    False when no call is open: an unpaired shorthand response is NOT a call, and
    falls through to the narrated lane it has always been in.
    """
    g = m.groupdict()
    idx = _take_open(state.open_binding, "sh:" + str(g.get("name")))
    body = _call_body(g)
    if idx is None:
        return False
    state.bindings[idx].update({**_call_closing(g, body), "endSeq": line.seq})
    return True
