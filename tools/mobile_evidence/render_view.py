"""The per-case view of a loaded evidence bundle: tallies computed once."""

from __future__ import annotations

from collections import Counter

from tools.mobile_evidence import model


def content_of(loaded: object) -> dict:
    body = loaded.get("content") if isinstance(loaded, dict) else None
    return (
        body if isinstance(body, dict) else {"source": "none", "reason": "not loaded"}
    )


def has_evidence(loaded: object) -> bool:
    return content_of(loaded).get("source") != "none"


def view_of(loaded: object, tc_id: object) -> dict | None:
    return content_of(loaded).get("by_case", {}).get(str(tc_id or ""))


def _gather(turns: list, key: str) -> list:
    return [x for t in turns for x in t.get(key) or []]


def _one(call: dict, rates: dict | None) -> tuple:
    """(model name, tokens in, tokens out, priced cost, unpriced count) of one call."""
    tok = call.get("tokens") or {}
    name = tok.get("model") or call.get("model") or "(unrecorded)"
    tin = int(tok.get("in") or 0)
    tout = int(tok.get("out") or 0)
    priced = model.price(name, model.TokenCount(tok.get("in"), tok.get("out")), rates)
    if priced is not None:
        return name, tin, tout, priced, 0
    return name, tin, tout, 0.0, 1 if tok else 0


def _tally(llm: list, rates: dict | None) -> dict:
    """In/out tokens, priced cost (None when nothing priced) and the model counter."""
    tin = tout = unpriced = 0
    cost = 0.0
    models: Counter = Counter()
    for call in llm:
        name, i, o, c, u = _one(call, rates)
        models[name] += 1
        tin += i
        tout += o
        cost += c
        unpriced += u
    return {
        "in": tin,
        "out": tout,
        "cost": cost if llm and unpriced < len(llm) else None,
        "unpriced": unpriced,
        "models": models,
    }


def case_view(content: dict, case: dict) -> dict:
    """Everything a card, a chip and a table need for ONE case, computed once."""
    ev = content["evidence"]
    turns = ev.for_case(case)
    llm, bindings, tools = (_gather(turns, k) for k in ("llm", "bindings", "tools"))
    window = ev.window_of(str(case.get("tc_id") or "")) or {}
    return {
        "turns": turns,
        "llm": llm,
        "bindings": bindings,
        "tools": tools,
        **_tally(llm, content.get("rates")),
        "exch": len(llm) + len(bindings) + len(tools),
        "window": window,
    }
