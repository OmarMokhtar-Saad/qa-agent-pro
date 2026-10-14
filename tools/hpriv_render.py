"""The tester-facing half of the host-privilege probe: step rows, sentence, content."""

from __future__ import annotations

from tools.hpriv_consts import DISPLAY, REFRESH_HINT
from tools.hpriv_steps import STEPS


def _attemptability_answer(step: dict, verdict: dict) -> str:
    """The three-valued ``attemptable`` answer for one applicable step."""
    if not step["needs_admin"]:
        answer = "yes"
    elif verdict.get("elevated") or verdict.get("can_elevate"):
        answer = "yes"
    elif verdict.get("undetermined"):
        answer = "unknown"
    else:
        answer = "no"
    return answer


# INAPPLICABLE IS NOT A THREE-VALUED ANSWER, it is not a row. Marking
# it would add a fourth value every consumer must learn; dropping it
# says the same thing and needs no branch anywhere. A step with no
# ``os`` applies everywhere, and an unknown host OS shows everything --
# failing OPEN, because hiding a step a tester could have taken is
# worse than showing one they cannot.
def _applies(step: dict, host_os: str) -> bool:
    """Whether *step* applies on *host_os* (unknown on either side: it does)."""
    step_os = str(step.get("os") or "")
    return not (step_os and host_os and step_os != host_os)


def _row(step: dict, verdict: dict) -> dict:
    """One step joined to the verdict."""
    return {
        "step": step["step"],
        "needs_admin": bool(step["needs_admin"]),
        "needs_network": bool(step.get("needs_network")),
        "attemptable": _attemptability_answer(step, verdict),
        "admin_free_route": step["admin_free_route"],
    }


# ``STEPS`` joined to a verdict. ``attemptable`` is THREE-VALUED.
#
# ``"unknown"`` rather than ``"no"`` on an undetermined verdict: a step this
# server would happily propose today must not read as blocked because a probe
# could not answer.
#
# STEPS ARE FILTERED BY THE HOST OS FIRST. ``attemptable`` answers "can this
# account do it", which is a question only worth asking about a step that
# applies here at all.
#
# ``attemptable`` SPEAKS ONLY TO PRIVILEGE. This module probes whether the
# account can elevate and nothing else, so "yes" means "your rights do not
# block this" -- not "this will work". A step that fetches something also
# needs the fetch to succeed, and on a managed machine an authenticating
# proxy or a TLS-inspecting middlebox refuses or rewrites the download while
# the account's rights are perfectly fine. Those steps carry
# ``needs_network``, so a consumer can say what has and has not been
# established rather than letting "yes" imply a reachability check nobody
# ran.
def attemptability(verdict: dict) -> list[dict]:
    """``STEPS`` joined to a verdict; ``attemptable`` is three-valued."""
    host_os = str(verdict.get("os") or "")
    return [_row(step, verdict) for step in STEPS if _applies(step, host_os)]


_SENTENCES = (
    (
        "undetermined",
        "Whether this account can elevate on {} is UNDETERMINED -- the check "
        "could not be read, which is NOT the same as no administrator rights. "
        "Nothing is blocked by it.",
    ),
    ("elevated", "This session is already running elevated on {}."),
    (
        "can_elevate",
        "This account can elevate on {} when asked (it is not elevated right now).",
    ),
)
_CANNOT = (
    "This account cannot elevate on {}, so use the admin-free route below "
    "or hand the admin step to IT."
)


def verdict_sentence(content: dict) -> str:
    """The one sentence every consumer renders, so three of them cannot drift."""
    name = DISPLAY.get(str(content.get("os")), "this OS")
    for flag, text in _SENTENCES:
        if content.get(flag):
            return text.format(name)
    return _CANNOT.format(name)


def build_content(core: dict, os_name: str, arch: str) -> dict:
    """The ten-field ``content`` dict (key order is the JSON contract)."""
    content = {
        "os": os_name,
        "arch": arch,
        "elevated": bool(core["elevated"]),
        "can_elevate": bool(core["can_elevate"]),
        "undetermined": bool(core["undetermined"]),
        "method": str(core["method"]),
        "cached": False,
        "refresh_hint": REFRESH_HINT,
    }
    content["summary"] = verdict_sentence(content)
    content["steps"] = attemptability(content)
    return content
