"""``parse``: one pass over the events, each claimed by the first stream that reads it."""

from __future__ import annotations

from tools.mobile_evidence.grammar_bind import (
    _on_bind_err,
    _on_bind_req,
    _on_bind_res,
    _on_bind_retry,
)
from tools.mobile_evidence.grammar_book import _enter_run, _record_run, _record_turn
from tools.mobile_evidence.grammar_call import _on_call_req, _on_call_res
from tools.mobile_evidence.grammar_llm import (
    _on_llm_err,
    _on_llm_frame,
    _on_llm_req,
    _on_llm_res,
    _on_prompt_msg,
    _on_usage,
)
from tools.mobile_evidence.grammar_narr import (
    _on_agent_answer,
    _on_agent_turn,
    _on_card_push,
    _on_flow_state,
    _on_note,
    _on_tool_done,
    _on_tool_invoke,
)
from tools.mobile_evidence.grammar_net import GrammarKit, merge_logcat_network
from tools.mobile_evidence.grammar_pat import (
    _disabled_streams,
    _Grammar,
    _grammar_of,
    _match,
)
from tools.mobile_evidence.grammar_result import _parse_result
from tools.mobile_evidence.grammar_runlog import _on_failure, _runlog_entry
from tools.mobile_evidence.grammar_state import _line_of, _ParseState
from tools.mobile_evidence.grammar_structured import _on_structured

# The prose matchers, tried in this order after the structured events. The order is the
# behaviour: the first handler that returns True claims the line. ``call_res`` (unpaired),
# ``prompt_msg`` and ``llm_frame`` (no open call) return False, so the later ones still run.
_PROSE_ARMS = (
    ("bind_req", _on_bind_req),
    ("call_req", _on_call_req),
    ("call_res", _on_call_res),
    ("bind_res", _on_bind_res),
    ("bind_err", _on_bind_err),
    ("bind_retry", _on_bind_retry),
    ("llm_req", _on_llm_req),
    ("prompt_msg", _on_prompt_msg),
    ("llm_frame", _on_llm_frame),
    ("llm_res", _on_llm_res),
    ("llm_err", _on_llm_err),
    ("usage", _on_usage),
    ("note", _on_note),
    ("agent_turn", _on_agent_turn),
    ("agent_answer", _on_agent_answer),
    ("tool_invoke", _on_tool_invoke),
    ("tool_done", _on_tool_done),
    ("flow_state", _on_flow_state),
    ("card_push", _on_card_push),
)


def _parse_event(state: _ParseState, ev, gram: _Grammar) -> None:
    """Read one event: bookkeeping, then the first stream that claims it, else the run log."""
    _enter_run(state, ev)
    line = _line_of(ev, state.run_index)
    _record_run(state, line)
    _record_turn(state, line)
    # Structured events first: they carry fields the prose cannot.
    if _on_structured(state, line, gram):
        return
    for name, handler in _PROSE_ARMS:
        m = _match(gram.compiled, name, line.msg)
        if m and handler(state, line, m):
            return
    _on_failure(state, line, gram)
    # The fall-through stream: every narrated line no matcher claimed, kept whole.
    entry = _runlog_entry(line, gram)
    if entry is not None:
        state.runlog.append(entry)


def parse(events, logcat_lines, kit: GrammarKit) -> dict:
    """Every stream the profile's grammar can read out of ``events``.

    ``logcat_lines`` (optional) is the logcat capture beside a structured log: its network
    stream is spliced in first (see :func:`merge_logcat_network`). The result's
    ``disabled`` lists every stream the profile carried no pattern for.
    """
    events = list(events or [])
    merged = 0
    if logcat_lines:
        merged = merge_logcat_network(events, logcat_lines, kit)
    gram, state = (
        _grammar_of(kit.profile, kit.compiled),
        _ParseState(compiled=kit.compiled),
    )
    for ev in events:
        _parse_event(state, ev, gram)
    return _parse_result(state, merged, _disabled_streams(kit.compiled))
