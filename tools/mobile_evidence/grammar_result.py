"""The result of one ``parse``: tool joining, counting and the fixed key order."""

from __future__ import annotations

from tools.mobile_evidence.grammar_state import _ParseState


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


def _unresolved(state: _ParseState) -> list:
    """The binding names of every call still open when the events ran out."""
    return [
        state.bindings[i]["binding"] for q in state.open_binding.values() for i in q
    ]


def _stream_sections(state: _ParseState, tools: list) -> dict:
    """The first twelve keys of the result: the clock, then every stream."""
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
    }


def _ordered_turns(turns: dict) -> list:
    """The turn records in first-seq order."""
    return [turns[k] for k in sorted(turns, key=lambda k: turns[k]["firstSeq"] or 0)]


def _parse_result(state: _ParseState, merged: int, disabled: list) -> dict:
    """Join and count what the events left in ``state``, in the result's key order."""
    unresolved = _unresolved(state)
    _join_invocations(state.invocations, state.recovered)
    tools = _pick_tools(state.tools, state.invocations, state.recovered)
    _count_tools(tools, state.turns, state.runs)
    _count_turns(state.turns, state.runs)
    result = _stream_sections(state, tools)
    result.update(
        {
            "turns": _ordered_turns(state.turns),
            "appRuns": sorted(state.runs.values(), key=lambda r: r["runIndex"]),
            "unresolvedBindings": unresolved,
            "mergedNetworkLines": merged,
            "disabled": disabled,
        }
    )
    return result
