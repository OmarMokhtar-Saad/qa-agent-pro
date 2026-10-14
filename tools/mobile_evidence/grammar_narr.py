"""The narrated arms: notes, utterances, answers, tool invocations, flow states, cards."""

from __future__ import annotations

from tools.mobile_evidence.grammar_pat import _pat
from tools.mobile_evidence.grammar_state import _Line, _ParseState, _stamp
from tools.mobile_evidence.grammar_values import _has_rtl, _int_or_none


def _on_note(state: _ParseState, line: _Line, m) -> bool:
    """Record a ``note`` match with its reading direction. Always True."""
    g = m.groupdict()
    state.notes.append(
        {
            **_stamp(line),
            "noteId": g.get("id"),
            "text": g.get("text"),
            "lang": "rtl" if _has_rtl(line.msg) else "ltr",
        }
    )
    return True


def _on_agent_turn(state: _ParseState, line: _Line, m) -> bool:
    """Record an ``agent_turn`` match as an utterance. Always True."""
    g = m.groupdict()
    state.utterances.append(
        {
            **_stamp(line),
            "text": g.get("text"),
            "history": _int_or_none(g.get("history")),
        }
    )
    return True


def _on_agent_answer(state: _ParseState, line: _Line, m) -> bool:
    """Record an ``agent_answer`` match. Always True."""
    state.answers.append({**_stamp(line), "text": m.groupdict().get("text")})
    return True


def _on_tool_invoke(state: _ParseState, line: _Line, m) -> bool:
    """Open a narrated tool invocation from a ``tool_invoke`` match. Always True."""
    g = m.groupdict()
    state.invocations.append(
        {
            **_stamp(line),
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


def _on_tool_done(state: _ParseState, line: _Line, m) -> bool:
    """Close the newest open invocation of the same tool, if any. Always True."""
    g = m.groupdict()
    for entry in reversed(state.invocations):
        if entry["tool"] == g.get("tool") and entry["resultText"] is None:
            entry["resultText"] = g.get("result")
            entry["status"] = "done"
            entry["endSeq"] = line.seq
            break
    return True


def _on_flow_state(state: _ParseState, line: _Line, m) -> bool:
    """Record a ``flow_state`` match: its head fields, then the fixed keys. Always True."""
    g = m.groupdict()
    rest = g.get("rest") or ""
    head, _, note = rest.partition("note=")
    field_pat = _pat(state.compiled, "flow_field")
    record = dict(field_pat.findall(head)) if field_pat is not None else {}
    record.update(
        {
            **_stamp(line),
            "tool": g.get("tool"),
            "note": note.strip() or None,
        }
    )
    state.flow_states.append(record)
    return True


def _on_card_push(state: _ParseState, line: _Line, m) -> bool:
    """Record a ``card_push`` match. Always True."""
    g = m.groupdict()
    state.cards.append(
        {
            **_stamp(line),
            "kind": g.get("kind"),
            "replaced": g.get("replaced") == "true",
        }
    )
    return True
