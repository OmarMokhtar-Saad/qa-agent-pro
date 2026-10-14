"""Untrusted-context wrapping of tools.host_llm (split out of host_llm.py)."""

from __future__ import annotations

import logging

from tools import host_llm_consts as hk
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.host_llm")


def is_host_task_record(record: object) -> bool:
    """True when a loaded prep record is one of THIS module's task records.

    Host-task records share the preps table with generation preps, so the
    generation submit path calls this to say "that is a host-task id" instead of
    reporting a confusing corrupted-prep error. Never raises.
    """
    return isinstance(record, dict) and hk._MARKER in record


def _log_empty_wrap(kind: str, chars: int) -> None:
    logger.debug(
        "host_llm._wrap_context: a non-empty %s context (%d chars) wrapped "
        "down to an EMPTY body -- every character was an <untrusted_content> "
        "delimiter and was stripped as a spoof attempt, so the host gets no "
        "context for this task",
        kind,
        chars,
    )


# ENFORCEMENT, not a prose promise: everything in an envelope's user context is
# externally sourced (ticket text, comments, RAG hits, tester input), so it is
# labelled as data before it can ever be read as an instruction.
#
# There is deliberately NO "looks already wrapped, pass it through" branch, and
# nothing here branches on CONTENT. Sniffing for a ``<untrusted_content``
# substring would hand the decision to the attacker: ``tools/untrusted`` carries
# ``_SPOOF_PATTERN`` precisely because externally-sourced text (a Jira
# description, a comment, a RAG hit) can contain that exact literal, so a
# pass-through on it would let one poisoned ticket send the WHOLE context
# through unwrapped and un-spoof-stripped -- a direct violation of the CLAUDE.md
# hard rule. Wrapping twice is safe instead, and verified against
# ``tools/untrusted.wrap_untrusted``: it substitutes away every inner
# ``<untrusted_content ...>`` / ``</untrusted_content>`` tag in the body before
# re-wrapping, so an already-wrapped input comes back wrapped EXACTLY once, its
# (possibly forged) inner delimiters removed and its text intact. Content is
# never trusted to self-report its own status; if a future caller needs a
# genuine pass-through it must declare it with an explicit parameter, never by
# having its data recognised.
#
# TWO CONSEQUENCES a caller (Phase 4 especially) must design around. Both are
# deliberate, both follow from the wrap being unconditional, and neither is a
# bug:
#
# 1. **Per-source provenance is FLATTENED.** A caller that assembles several
#    already-wrapped blocks into one context string -- ``source="jira"`` plus
#    ``source="rag"`` plus ``source="web"`` -- gets ONE block back, labelled
#    ``source="host_task_<kind>"``, because every inner delimiter is stripped
#    first. The text survives and stays contained, but the per-source labels do
#    NOT. A caller that needs the host to tell its sources apart must carry
#    that in the body (a plain ``--- from Jira ---`` heading), never in tags.
# 2. **An all-delimiter body wraps down to NOTHING.** A context made only of
#    ``<untrusted_content>`` tags and whitespace has every character removed as
#    a spoof attempt, so the host receives a wrapper with an empty body (and a
#    blank input returns ``""`` outright). That is the correct security
#    outcome, but it is silent, so it is logged at DEBUG below: a caller whose
#    prompt suddenly has no context can find out why instead of guessing.
def _wrap_context(kind: str, user: str) -> str:
    """Wrap the user context as UNTRUSTED, UNCONDITIONALLY. Never raises."""
    text = str(user or "")
    if not text.strip():
        return ""
    wrapped = wrap_untrusted(f"host_task:{kind}", text, limit=hk._MAX_CONTEXT_CHARS)
    # wrap_untrusted emits open tag, body, close tag: body sits between the newlines.
    body = wrapped.partition("\n")[2].rpartition("\n")[0] if wrapped else ""
    if not body.strip():
        _log_empty_wrap(kind, len(text))
    return wrapped
