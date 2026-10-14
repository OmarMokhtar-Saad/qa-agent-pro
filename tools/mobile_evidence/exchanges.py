"""One call -- an endpoint, a model round-trip, a tool -- as the shell's exchange row.

Two halves, both ported from the reference. The ROW SHAPE (:func:`binding_exchange`,
:func:`network_exchange`, :func:`llm_exchange`, :func:`tool_exchange`) turns a parsed
record into the small dict every exchange row reads: method, path, url, status, ok,
statusLabel, durationMs and the query/header/body on each side, with ``notCaptured``
saying per field what the app never logs -- a blank pane would read as "the call
carried none of that" when the truth is "the log never has it". The MARKUP
(:func:`exchange_row`, :func:`seq_exchange`, the ``seq_*`` primitives,
:func:`seqlist`) is the reference shell's vocabulary, so a call reads the same on
this report as on the one it was copied from.

Every string reaches markup through :func:`scrub.e`. There is no lazy text store
here -- the reference parks big bodies in script islands; this report never emits a
script the shell did not ship -- so bodies are inline and capped.

DURATIONS ARE DERIVED, NOT MEASURED. A request and its response are two log
events; the evidence layer stamps the interval as ``derivedMs``, read here in
preference to ``durationMs`` (which the app emits for structured tool events and
rarely fills). A record with neither draws NO bar: a bar of width zero is a
measurement of zero.

This module knows no app: the words on a row come from the record or from the
caller, never from a constant here.
"""

from __future__ import annotations

from tools.mobile_evidence.exch_bind import (  # noqa: F401
    _binding_extra,
    _binding_request_body,
    _binding_response_body,
    binding_exchange,
)
from tools.mobile_evidence.exch_body import (  # noqa: F401
    _JSON_TOK,
    BODY_LIMIT,
    _hl_json,
    _sniff,
    codebox,
)
from tools.mobile_evidence.exch_head import _ex_head, host_of  # noqa: F401
from tools.mobile_evidence.exch_kv import kvtable, section  # noqa: F401
from tools.mobile_evidence.exch_llm import (  # noqa: F401
    _llm_bodies,
    _llm_meta,
    _llm_outcome,
    llm_exchange,
)
from tools.mobile_evidence.exch_net import network_exchange  # noqa: F401
from tools.mobile_evidence.exch_num import _duration, dur_ms, fmt_ms  # noqa: F401
from tools.mobile_evidence.exch_pane import (  # noqa: F401
    _ex_bits,
    exchange_row,
    exlist,
    seq_exchange,
)
from tools.mobile_evidence.exch_seqargs import (  # noqa: F401
    CapParts,
    FlowParts,
    LogParts,
    PromptNote,
    SecMeta,
    SeqRow,
    SessionParts,
    ToolParts,
)
from tools.mobile_evidence.exch_seqcall import (  # noqa: F401
    seq_cap,
    seq_flow,
    seq_log,
    seq_tool,
)
from tools.mobile_evidence.exch_seqk import SEQ_CUT, SEQ_RANK  # noqa: F401
from tools.mobile_evidence.exch_seqrow import (  # noqa: F401
    ms_clock,
    no_clock,
    seq_row,
    seq_session,
    seqlist,
)
from tools.mobile_evidence.exch_sequser import (  # noqa: F401
    seq_card,
    seq_note,
    seq_prompt,
    seq_reply,
    seq_step,
    seq_turn,
    seq_user,
)
from tools.mobile_evidence.exch_shape import _path_of, body_text  # noqa: F401
from tools.mobile_evidence.exch_tool import (  # noqa: F401
    _tool_request_body,
    _tool_result,
    tool_exchange,
)
from tools.mobile_shared.scrub import e  # noqa: F401
