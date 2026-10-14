"""The narrated binding arms: request, response, error and retry of one call."""

from __future__ import annotations

from tools.mobile_evidence.grammar_consts import RE_TRUNC_TAIL
from tools.mobile_evidence.grammar_state import (
    _bump,
    _Line,
    _ParseState,
    _stamp,
    _take_open,
)
from tools.mobile_evidence.grammar_values import _int_or_none, _maybe_json


def _binding_entry(line: _Line, g: dict) -> dict:
    """The record of a narrated binding call that has just been requested."""
    return {
        **_stamp(line),
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


def _on_bind_req(state: _ParseState, line: _Line, m) -> bool:
    """Open a narrated binding call from a ``bind_req`` match; always True."""
    g = m.groupdict()
    state.bindings.append(_binding_entry(line, g))
    state.open_binding.setdefault(g.get("name"), []).append(len(state.bindings) - 1)
    _bump(state, line, "bindings")
    return True


def _bind_response(g: dict) -> dict:
    """The response of a ``bind_res`` match: its text, the omitted tail and the JSON."""
    body = g.get("body") or ""
    clipped = RE_TRUNC_TAIL.search(body)
    return {
        "text": RE_TRUNC_TAIL.sub("", body) if clipped else body,
        "omitted": int(clipped.group(1)) if clipped else 0,
        "json": _maybe_json(RE_TRUNC_TAIL.sub("", body)),
    }


def _orphan_binding(line: _Line, name) -> dict:
    """The opening keys of a binding record made for a response with no request."""
    return {
        **_stamp(line),
        "binding": name,
        "verb": None,
        "url": None,
        "args": None,
    }


def _close_binding(target: dict, entry: dict, seq) -> None:
    """Mark ``target`` answered: success is known, the exact 2xx is NOT (never a made-up 200)."""
    target.update(
        {
            "ok": True,
            "status": None,
            "statusKnown": False,
            "response": entry,
            "endSeq": seq,
        }
    )


def _on_bind_res(state: _ParseState, line: _Line, m) -> bool:
    """Close the open binding of a ``bind_res`` match in place, or add an orphan; True."""
    g = m.groupdict()
    idx = _take_open(state.open_binding, g.get("name"))
    entry = _bind_response(g)
    if idx is not None:
        _close_binding(state.bindings[idx], entry, line.seq)
        return True
    target = _orphan_binding(line, g.get("name"))
    target.update({"orphanResponse": True, "retried": False})
    _close_binding(target, entry, line.seq)
    state.bindings.append(target)
    _bump(state, line, "bindings")
    return True


def _error_detail(line: _Line, g: dict) -> dict:
    """The failure fields of a ``bind_err`` match."""
    return {
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


def _binding_error(state: _ParseState, line: _Line, binding: dict) -> None:
    """Record a failed binding; the detail is the binding object itself, never a copy."""
    state.errors.append({"seq": line.seq, "kind": "binding", "detail": binding})


def _on_bind_err(state: _ParseState, line: _Line, m) -> bool:
    """Fail the open binding of a ``bind_err`` match, or add an orphan; True."""
    g = m.groupdict()
    idx = _take_open(state.open_binding, g.get("name"))
    detail = _error_detail(line, g)
    if idx is not None:
        state.bindings[idx].update(detail)
        _binding_error(state, line, state.bindings[idx])
    else:
        orphan = _orphan_binding(line, g.get("name"))
        orphan.update({"retried": False, "orphanResponse": True})
        orphan.update(detail)
        state.bindings.append(orphan)
        _binding_error(state, line, orphan)
        _bump(state, line, "bindings")
    _bump(state, line, "errors")
    return True


def _on_bind_retry(state: _ParseState, line: _Line, m) -> bool:
    """Mark the oldest open call of a ``bind_retry`` match as retried; always True."""
    queue = state.open_binding.get(m.groupdict().get("name"))
    if queue:
        state.bindings[queue[0]]["retried"] = True
    return True
