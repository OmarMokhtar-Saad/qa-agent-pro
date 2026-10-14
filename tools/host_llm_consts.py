"""Scalar constants of tools.host_llm (one home; split out of host_llm.py)."""

from __future__ import annotations

# Marker + version stamped into every stored task record. Records share the preps
# TABLE with generation preps, so the marker is also the NAMESPACE: it is how a
# host-task id is recognised (``is_host_task_record``) and reported as such
# instead of surfacing from the generation submit path as a confusing "corrupted
# prep". A record persists across restarts AND auto-updates, so a wrong/absent
# marker is rejected exactly like a stale id rather than half-rehydrated. Bump on
# any incompatible shape change.
_MARKER = "__host_llm__"
_SCHEMA_VERSION = 1

# created_by value stamped on every task record, so the store can be filtered by
# origin without parsing payloads.
_CREATED_BY = "host_llm"

# Hard cap on a host submission before it is parsed at all. Generous (a full bug
# report or feature-analysis JSON is a few KB) but finite, so a pathological
# submission cannot wedge the parser.
_MAX_RAW_CHARS = 400_000

# Per-field cap applied to the PARSED payload before a caller ever sees it, plus
# collection caps. The whole-submission cap above stops a wedge; these stop one
# enormous string field from riding into an export, a Jira write or a prompt.
_MAX_FIELD_CHARS = 20_000
_MAX_ITEMS = 500
_MAX_DEPTH = 6

# Cap on the prompt context wrapped into an envelope. Large enough for a fully
# grounded Jira + RAG prompt, finite so an envelope cannot exceed QA_PREP_MAX_BYTES
# territory.
_MAX_CONTEXT_CHARS = 200_000

# Task kinds this broker knows. A fixed set, checked at open time: no
# payload-driven dispatch, no dynamic lookup.
_KNOWN_KINDS = frozenset(
    {
        "bug_report",
        "explore_step",
        "feature_analysis",
        "translate_cases",
        "api_test",
        "api_contract_fill",
        "generic",
    }
)

# There is deliberately NO default submit-tool name -- see the PHASE NOTE above.
# Each caller names its own dedicated submit tool (``qa_submit_bug_report``,
# ``qa_submit_explore_step``, ``qa_submit_feature_analysis``). A shared generic
# submit tool was judged REDUNDANT (nothing calls one; each kind needs its own
# submit-side side effects), not impossible, so an envelope built without a name
# points the host at the tool named in the surrounding reply rather than at one
# that was never registered.
