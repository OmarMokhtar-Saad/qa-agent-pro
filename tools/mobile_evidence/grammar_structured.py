"""The structured-event arms: tool, cost and config events, and usage attachment."""

from __future__ import annotations

from typing import cast

from tools.mobile_evidence.grammar_pat import _Grammar, _msg_in
from tools.mobile_evidence.grammar_state import _bump, _Line, _ParseState, _stamp
from tools.mobile_evidence.grammar_values import _body_shape, _int_or_none


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


def _claims(line: _Line, cat, msgs) -> bool:
    """Is ``line`` an event of structured category ``cat`` whose message is in ``msgs``?"""
    return bool(cat and line.cat == cat and _msg_in(line.msg, msgs))


def _tool_entry(line: _Line) -> dict:
    """The tool record of a structured tool event."""
    msg, fields = line.msg, line.fields
    return {
        **_stamp(line),
        "tool": fields.get("tool"),
        "status": msg.split(".", 1)[1] if "." in msg else msg,
        "kind": fields.get("kind"),
        "args": _body_shape(fields.get("body.args")),
        "durationMs": line.ev.get("durationMs"),
    }


def _on_tool_event(state: _ParseState, line: _Line, gram: _Grammar) -> bool:
    """Record a structured tool event; True when ``line`` was one."""
    if not _claims(line, gram.tool_cat, gram.tool_msgs):
        return False
    entry = _tool_entry(line)
    state.tools.append(entry)
    _bump(state, line, "tools")
    if entry["status"] == "failed":
        state.errors.append({"seq": line.seq, "kind": "tool", "detail": entry})
        _bump(state, line, "errors")
    return True


def _orphan_call(line: _Line, usage: dict) -> dict:
    """An llm record made of usage alone, for usage that matches no open call."""
    return {
        **_stamp(line),
        "model": usage["model"],
        "tokens": usage,
        "promptMessages": [],
        "orphanUsage": True,
    }


def _attach_usage(state: _ParseState, line: _Line, usage: dict) -> None:
    """Attach ``usage`` to the open llm call, merge it into the last, or add an orphan."""
    if state.open_llm is not None and state.llm[state.open_llm].get("tokens") is None:
        state.llm[state.open_llm]["tokens"] = usage
        state.llm[state.open_llm]["endSeq"] = line.seq
        state.last_usage = state.llm[state.open_llm]["tokens"]
    elif _same_usage(state.last_usage, usage):
        # The same round-trip, reported twice (prose then structured): the
        # structured form replaces the prose in place, never appends.
        cast("dict", state.last_usage).update(usage)
    else:
        state.llm.append(_orphan_call(line, usage))
        state.last_usage = state.llm[-1]["tokens"]


def _cost_usage(line: _Line) -> dict:
    """The usage record of a structured cost event."""
    msg, fields = line.msg, line.fields
    return {
        **_stamp(line),
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


def _on_cost_event(state: _ParseState, line: _Line, gram: _Grammar) -> bool:
    """Record a structured cost event as llm usage; True when ``line`` was one."""
    if not _claims(line, gram.cost_cat, gram.cost_msgs):
        return False
    _attach_usage(state, line, _cost_usage(line))
    state.open_llm = None
    return True


def _on_config_event(state: _ParseState, line: _Line, gram: _Grammar) -> bool:
    """Record a structured config event; True when ``line`` was one."""
    if not _claims(line, gram.config_cat, gram.config_msgs):
        return False
    state.configs.append({**_stamp(line), "fields": dict(line.fields)})
    return True


def _on_structured(state: _ParseState, line: _Line, gram: _Grammar) -> bool:
    """Try the tool, cost and config arms in that order; True when one took ``line``."""
    return (
        _on_tool_event(state, line, gram)
        or _on_cost_event(state, line, gram)
        or _on_config_event(state, line, gram)
    )
