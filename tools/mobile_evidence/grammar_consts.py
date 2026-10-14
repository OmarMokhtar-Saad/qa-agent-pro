"""Constants of the capture grammar: schema id, stream names, logcat patterns, named gaps."""

from __future__ import annotations

import re

SCHEMA = "qa-agents.mobile-evidence.report/1"

#: The parse streams a profile may carry a pattern for, by name. The names are the
#: engine's; the text behind each is the profile's.
STREAMS = (
    "bind_req",
    "bind_res",
    "bind_err",
    "bind_retry",
    "call_req",
    "call_res",
    "llm_req",
    "llm_res",
    "llm_frame",
    "llm_err",
    "prompt_msg",
    "prompt_user",
    "usage",
    "note",
    "agent_turn",
    "agent_answer",
    "agent_fail",
    "tool_invoke",
    "tool_done",
    "flow_state",
    "flow_field",
    "card_push",
    "net",
    "hash_head",
    "served_model",
    "tool_call",
    "data_line",
)

# --- generic transport patterns (engine-owned) ---

#: One logcat line, in either of the two formats adb produces.
#:   threadtime  ``MM-DD HH:MM:SS.mmm  PID  TID L TAG: msg``   (``-v threadtime``)
#:   brief       ``MM-DD HH:MM:SS.mmm L/TAG( PID): msg``        (adb's default)
RE_LOGCAT = re.compile(
    r"^(?P<ts>\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEF]) (?P<tag>[^:]+?): (?P<msg>.*)$"
)
RE_LOGCAT_BRIEF = re.compile(
    r"^(?P<ts>\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+"
    r"(?P<level>[VDIWEF])/(?P<tag>[^(]+?)\(\s*(?P<pid>\d+)\): (?P<msg>.*)$"
)
#: ``MM-DD HH:MM:SS.mmm`` at the head of a logcat line; no year, no zone.
RE_SLICE_TS = re.compile(r"^(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\.(\d{3})")
#: A structured event flattened to logcat as ``msg k=v k=v``; split only where a
#: plausible field name starts, because a VALUE may hold spaces and ``=``.
RE_KV_BOUNDARY = re.compile(r"\s+(?=[A-Za-z][A-Za-z0-9_.]*=)")
#: Body-policy notations.
RE_BODY_HASHED = re.compile(r"^\[len (\d+) h:([0-9a-f]{4})\]$")
RE_BODY_TRUNC = re.compile(r"\u2026\[\+(\d+) chars\]$")
RE_TRUNC_TAIL = re.compile(r"\u2026\(\+(\d+) more\)$")

#: Named gaps every capture of this kind has; stated so a report can say them.
GAPS = [
    {
        "id": "httpStatusOnSuccess",
        "what": "HTTP status code for a successful request",
        "why": "the SDK logs the status only on the failure branch; the success branch "
        "logs the body alone, so a 200 and a 204 are indistinguishable here.",
    },
    {
        "id": "httpDuration",
        "what": "a measured duration for any request",
        "why": "nothing times the call; durations on this page are DERIVED from the clock "
        "on the request line and the response line.",
    },
    {
        "id": "engineSnapshot",
        "what": "engine / flow state snapshots",
        "why": "there is no state event; the only state visible is whatever the narrated "
        "note text says, which is UI prose rather than a machine record.",
    },
]


def logcat_line(line: str):
    """One parsed logcat line, whichever of the two formats it is in, or None."""
    return RE_LOGCAT.match(line) or RE_LOGCAT_BRIEF.match(line)


_META = set(r"\^$.|?*+()[]{}")


_LOGCAT_FALLBACK_GAP = {
    "id": "logcatFallback",
    "what": "seq ordering, monotonic durations, and the turn correlation chain",
    "why": "this capture was parsed from logcat; those fields exist only in "
    "the structured event log, which was not captured.",
}
