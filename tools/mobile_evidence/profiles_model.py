"""The per-app profile vocabulary: stream names, limits and the ``Profile`` record.

Split out of ``profiles.py`` (which stays the public facade). No app is named here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROFILES_DIR = Path(__file__).with_name("profiles")

#: Every stream the grammar can read. A profile may leave any of them out; the ones in
#: REQUIRED_STREAMS make the difference between "some evidence" and "no usable evidence".
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

#: The minimum for a parse worth showing: a model request, its reply, a usage line and
#: the turn boundary that attributes them.
REQUIRED_STREAMS = ("llm_req", "llm_res", "usage", "agent_turn")

#: Patterns that must match across lines: the app logs a multi-line body as ONE event.
DOTALL_STREAMS = frozenset(
    {
        "bind_res",
        "bind_err",
        "call_res",
        "llm_res",
        "llm_frame",
        "llm_err",
        "prompt_msg",
        "prompt_user",
        "note",
        "agent_turn",
        "agent_answer",
        "agent_fail",
        "tool_invoke",
        "tool_done",
        "flow_state",
    }
)

MAX_PROFILE_BYTES = 256 * 1024


@dataclass
class Profile:
    """One app's vocabulary. Every field has a safe default so a partial file still loads."""

    name: str = ""
    package: str = ""
    log_dir_pattern: str = ""
    segment_name_regex: str = ""
    logcat_tag: str = ""
    log_prefix: str = ""
    patterns: dict = field(default_factory=dict)
    turn_markers: dict = field(default_factory=dict)
    log_failures: list = field(default_factory=list)
    runlog_lanes: list = field(default_factory=list)
    runlog_bad: list = field(default_factory=list)
    env_by_host: dict = field(default_factory=dict)
    rates: dict = field(default_factory=dict)
    rates_note: str = ""
    structured: dict = field(default_factory=dict)
    source: str = ""

    def log_dir(self) -> str:
        return str(self.log_dir_pattern or "").replace("{package}", self.package)


#: What a profile-less capture calls itself, everywhere it is disclosed. It is
#: a NAME and not a flag: the report, the status block and the case record all
#: show it, so a tester never mistakes a raw logcat slice for a parsed one.
GENERIC_PROFILE_NAME = "generic profile"


# It knows no grammar, no on-device log directory and no logcat tag, so the
# dump is narrowed by PID alone -- and when the pid is unknown there is
# nothing to narrow by at all, which is why ``capture.slice_case`` refuses
# outright on that path rather than capturing every app on the device. This
# sentence used to argue the opposite, and a review that ran it proved the
# argument false. Every stream in ``STREAMS`` stays absent, so the sections that
# read them keep saying so; what changes is that the app's logcat is captured
# at all.
#
# Before this existed, an unprofiled package produced NO evidence: the case
# was recorded ``skipped``, and every later call short-circuited on that. The
# one app this lane was validated against on 2026-09-04 was unprofiled, so
# the whole evidence path was dead exactly where it was needed.
#
# ``profile_for`` deliberately does NOT fall back to this. Choosing the
# fallback is the caller's decision and a visible one; a lookup that silently
# started succeeding would make "no profile" unobservable.
def generic_profile(package: object) -> "Profile":
    """A profile for an app nobody has written one for."""
    return Profile(
        name=GENERIC_PROFILE_NAME,
        package=str(package or ""),
        source=GENERIC_PROFILE_NAME,
    )
