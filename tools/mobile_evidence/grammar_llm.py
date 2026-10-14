"""The llm arms: request, prompt messages, frames, response, error and prose usage."""

from __future__ import annotations

from typing import cast

from tools.mobile_evidence.grammar_pat import _match, _pat
from tools.mobile_evidence.grammar_state import (
    _bump,
    _error_entry,
    _Line,
    _ParseState,
    _stamp,
)
from tools.mobile_evidence.grammar_structured import _attach_usage
from tools.mobile_evidence.grammar_values import _body_shape, _int_or_none


def _prompt_tail(state: _ParseState, tail: str, compiled) -> None:
    """Add the prompt messages on ``tail`` to the open llm call.

    This is the structured path: the messages ride on the same record. A body
    that spans lines is appended to the message before it.
    """
    for piece in tail.split("\n"):
        _p = _match(compiled, "prompt_msg", piece)
        if _p:
            state.llm[cast("int", state.open_llm)]["promptMessages"].append(
                _body_shape(_p.group("body"))
            )
        elif state.llm[cast("int", state.open_llm)]["promptMessages"] and piece.strip():
            _prev = state.llm[cast("int", state.open_llm)]["promptMessages"][-1]
            if isinstance(_prev, dict) and isinstance(_prev.get("text"), str):
                _prev["text"] += "\n" + piece


def _llm_entry(line: _Line, g: dict) -> dict:
    """The record of an llm call that has just been requested."""
    return {
        **_stamp(line),
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


def _on_llm_req(state: _ParseState, line: _Line, m) -> bool:
    """Open an llm call from an ``llm_req`` match; always True."""
    g = m.groupdict()
    state.llm.append(_llm_entry(line, g))
    state.open_llm = len(state.llm) - 1
    _prompt_tail(state, line.msg[m.end() :], state.compiled)
    _bump(state, line, "llm")
    return True


def _recovered_call(line: _Line, call) -> dict:
    """A tool call recovered from the text of an llm response."""
    return {
        **_stamp(line),
        "tool": call.group("tool"),
        "status": "called",
        "kind": None,
        "args": _body_shape(call.group("args")),
        "durationMs": None,
        "argsRecovered": True,
    }


def _on_llm_res(state: _ParseState, line: _Line, m) -> bool:
    """Set the open call's response and recover the tool calls in it; always True."""
    body = m.groupdict().get("body") or ""
    if state.open_llm is not None:
        state.llm[state.open_llm]["response"] = _body_shape(body)
        state.llm[state.open_llm]["endSeq"] = line.seq
    tool_call = _pat(state.compiled, "tool_call")
    if tool_call is not None:
        for call in tool_call.finditer(body):
            state.recovered.append(_recovered_call(line, call))
    return True


def _on_llm_err(state: _ParseState, line: _Line, m) -> bool:
    """Record an ``llm_err`` match; it closes the open call only before a response."""
    g = m.groupdict()
    err = {"class": g.get("cls"), "message": g.get("msg")}
    if state.open_llm is not None and state.llm[state.open_llm].get("response") is None:
        state.llm[state.open_llm]["error"] = err
        state.llm[state.open_llm]["endSeq"] = line.seq
        state.open_llm = None
    state.errors.append(_error_entry(line, "llm", err))
    _bump(state, line, "errors")
    return True


def _on_prompt_msg(state: _ParseState, line: _Line, m) -> bool:
    """Add a ``prompt_msg`` match to the open llm call; False when no call is open."""
    if state.open_llm is None:
        return False
    state.llm[state.open_llm]["promptMessages"].append(_body_shape(m.group("body")))
    return True


def _on_llm_frame(state: _ParseState, line: _Line, m) -> bool:
    """Count an ``llm_frame`` match on the open llm call; False when no call is open."""
    if state.open_llm is None:
        return False
    state.llm[state.open_llm]["frames"] += 1
    return True


def _on_usage(state: _ParseState, line: _Line, m) -> bool:
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
