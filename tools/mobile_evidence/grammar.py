"""Turn an app's captured log into structured streams -- with the vocabulary supplied.

Two inputs, one grammar. An app SDK may write a structured NDJSON event log to its
private storage and a prose narration to logcat; the same message text reaches both,
so both are read here, the structured shape preferred when present. Nothing in this
module knows what that prose LOOKS like: every pattern for the app's own narration --
the request/response pair stream, the model round-trip stream, the usage line, the
turn-start marker, the tool-invocation and tool-result lines, the flow-state and card
lines, the network line -- is compiled from a ``Profile`` (``profiles.py``) and looked up
by STREAM NAME. What lives here is the part that is the same for every app:

  * the two Android logcat line formats (threadtime and brief);
  * the shape of a category prefix (``<prefix> - [CAT]``), built from the profile's prefix;
  * the ``k=v k=v`` flattening a structured event undergoes on its way to logcat;
  * the body-policy notations (``[len N h:xxxx]`` hashed, ``...[+N chars]`` truncated,
    ``...(+N more)`` clipped);
  * the NDJSON record fields (``seq`` is the ordering authority, ``ts`` display only);
  * the algorithms: request/response pairing oldest-first per name, usage twin merging,
    turn attribution from the turn-start marker, network-line splicing by a voted clock
    offset, token totals.

A stream whose pattern the profile does not carry is skipped and NAMED in the parse
result's ``disabled`` list, so a report can say "this capture cannot show X" rather than
show nothing. Nothing here raises to a caller: ``build`` returns ``{"error", "content"}``.

Every pattern is looked up through :func:`_pat`; a profile that misses one disables that
stream only. The engine may carry no glyph, no right-to-left codepoint and no word of any
vendor's vocabulary -- a test greps this file for exactly that.

The implementation lives in the grammar_* modules beside this file; this module only
re-exports their names, so every import path and patch target keeps working.
"""

from __future__ import annotations

from tools.mobile_evidence.grammar_bind import (  # noqa: F401
    _on_bind_err,
    _on_bind_req,
    _on_bind_res,
    _on_bind_retry,
)
from tools.mobile_evidence.grammar_book import (  # noqa: F401
    _enter_run,
    _record_run,
    _record_turn,
)
from tools.mobile_evidence.grammar_call import _on_call_req, _on_call_res  # noqa: F401
from tools.mobile_evidence.grammar_clock import (  # noqa: F401
    _clock_offset,
    _logcat_rows,
    _runlog_text,
    _wall_millis,
)
from tools.mobile_evidence.grammar_consts import (  # noqa: F401
    _LOGCAT_FALLBACK_GAP,
    _META,
    GAPS,
    RE_BODY_HASHED,
    RE_BODY_TRUNC,
    RE_KV_BOUNDARY,
    RE_LOGCAT,
    RE_LOGCAT_BRIEF,
    RE_SLICE_TS,
    RE_TRUNC_TAIL,
    SCHEMA,
    STREAMS,
    logcat_line,
)
from tools.mobile_evidence.grammar_llm import (  # noqa: F401
    _on_llm_err,
    _on_llm_frame,
    _on_llm_req,
    _on_llm_res,
    _on_prompt_msg,
    _on_usage,
    _prompt_tail,
)
from tools.mobile_evidence.grammar_narr import (  # noqa: F401
    _on_agent_answer,
    _on_agent_turn,
    _on_card_push,
    _on_flow_state,
    _on_note,
    _on_tool_done,
    _on_tool_invoke,
)
from tools.mobile_evidence.grammar_net import (  # noqa: F401
    GrammarKit,
    _network_event,
    merge_logcat_network,
)
from tools.mobile_evidence.grammar_parse import (  # noqa: F401
    _PROSE_ARMS,
    _parse_event,
    parse,
)
from tools.mobile_evidence.grammar_pat import (  # noqa: F401
    _disabled_pairs,
    _disabled_streams,
    _Grammar,
    _grammar_of,
    _head_re,
    _literal_prefix,
    _looks_like_regex,
    _match,
    _msg_in,
    _names,
    _pat,
    _prefix_re,
)
from tools.mobile_evidence.grammar_prompts import (  # noqa: F401
    _logcat_messages,
    _logcat_prompts,
    _reassemble_prompts,
    system_prompts,
)
from tools.mobile_evidence.grammar_read import (  # noqa: F401
    _ndjson_event,
    read_logcat,
    read_ndjson,
)
from tools.mobile_evidence.grammar_report import (  # noqa: F401
    CaptureSources,
    _parsed_sections,
    _report_counts,
    _run_section,
    build,
    env_from_traffic,
)
from tools.mobile_evidence.grammar_result import (  # noqa: F401
    _count_tools,
    _count_turns,
    _join_invocations,
    _parse_result,
    _pick_tools,
)
from tools.mobile_evidence.grammar_runlog import (  # noqa: F401
    _on_failure,
    _runlog_entry,
    runlog_lane,
)
from tools.mobile_evidence.grammar_state import (  # noqa: F401
    _bump,
    _Line,
    _line_of,
    _ParseState,
    _take_open,
)
from tools.mobile_evidence.grammar_structured import (  # noqa: F401
    _attach_usage,
    _on_config_event,
    _on_cost_event,
    _on_structured,
    _on_tool_event,
    _same_usage,
)
from tools.mobile_evidence.grammar_totals import totals  # noqa: F401
from tools.mobile_evidence.grammar_turns import (  # noqa: F401
    _bounded_turn,
    _run_of,
    _stamp_turn_ids,
    _turn_bounds,
    attribute_turns,
    turn_of_seq,
)
from tools.mobile_evidence.grammar_values import (  # noqa: F401
    _body_shape,
    _has_rtl,
    _int_or_none,
    _lines_of,
    _maybe_json,
    _unflatten,
)
