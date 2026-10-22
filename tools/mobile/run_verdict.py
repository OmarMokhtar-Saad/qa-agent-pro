"""A verdict that always says why.

Every way a mobile run can stop (limits, a refusal loop, the stop tool, an idle
release, a dead run) ends in a :class:`Verdict` that names the stop reason or
the last refusal. ``unverified`` is only ever returned WITH a stated reason.

This module also owns the refusal log: every script refusal writes one INFO
line ``refused: <rule> <detail>`` so a refusal that exists only in a chat
transcript (run mrun-20261005-111528-aade28, chat #149) is in the server log
the next time.

Pure: no disk, no device, no model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from tools.mobile.run_verdict_refusal import (  # noqa: F401
    _MARKERS,
    DETAIL_CHARS,
    RULE_ACTION_COUNT,
    RULE_EMPTY,
    RULE_NOT_JSON,
    RULE_SCHEMA,
    RULE_TOTAL_WAIT,
    RULE_VERDICT_WORD,
    _clip,
    classify_refusal,
    log_refusal,
    rule_limit,
)

logger = logging.getLogger(__name__)

OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_UNVERIFIED = "unverified"

#: Characters of a stop reason or refusal kept in a verdict line.
REASON_CHARS = 300


@dataclass(frozen=True)
class Verdict:
    """``outcome`` is pass / fail / unverified; ``reason`` is never empty."""

    outcome: str
    reason: str
    evidence: tuple = ()
    #: Turns that ended failed / interrupted; recorded in ``final`` when set, so a
    #: "pass" that hid failures is told apart without parsing ``reason``.
    failed_turns: int = 0
    interrupted_turns: int = 0
    #: Typed inputs whose read-back was unknown (OTP, passwords); recorded when set.
    unconfirmed_inputs: int = 0

    def line(self) -> str:
        return self.outcome + ": " + self.reason


def _from_trace(trace: object) -> Optional[Verdict]:
    """A ``done`` the run itself reached: a Verdict, or {verdict, reason}."""
    if isinstance(trace, Verdict):
        return trace
    if not isinstance(trace, Mapping):
        return None
    word = str(trace.get("verdict") or "").lower()
    reason = _clip(trace.get("reason"), REASON_CHARS)
    if word in (OUTCOME_PASS, OUTCOME_FAIL):
        return Verdict(word, reason or "the run reported " + word, ("done verdict",))
    if word == "blocked":
        text = reason or "the run reported it was blocked"
        return Verdict(OUTCOME_UNVERIFIED, "blocked: " + text, ("done verdict",))
    return None


# The verdict for a run that stopped.
#
# A ``done`` verdict the run reached wins. Otherwise the run is
# ``unverified`` and the reason names the stop reason and, when there was
# one, the last refusal -- never a bare word.
def synthesize(
    stop_reason: str, last_refusal: str = "", trace_verdict: object = None
) -> Verdict:
    traced = _from_trace(trace_verdict)
    if traced is not None:
        return traced
    reason = (
        _clip(stop_reason, REASON_CHARS) or "the run ended without a recorded reason"
    )
    refusal = _clip(last_refusal, REASON_CHARS)
    if refusal:
        return Verdict(
            OUTCOME_UNVERIFIED, reason + "; last refusal: " + refusal, (refusal,)
        )
    return Verdict(OUTCOME_UNVERIFIED, reason, ())


def _judged_code(previous: Any, installed: Any, target: Any) -> Optional[tuple]:
    """(version_ok, sentence), or None when there is nothing to judge."""
    if installed is None:
        return None
    if target is not None:
        ok = installed >= target
        word = " reached" if ok else " is below"
        return ok, "versionCode " + str(installed) + word + " the target " + str(target)
    if previous is None:
        return None
    if installed > previous:
        return True, "versionCode rose from " + str(previous) + " to " + str(installed)
    return False, "versionCode " + str(installed) + " did not rise above " + str(
        previous
    )


@dataclass(frozen=True)
class InstallFacts:
    previous_code: Optional[int]
    installed_code: Optional[int]
    target_code: Optional[int]
    foreground_package: str
    package: str


def _install_reason(text: str, foreground: str, package: str) -> str:
    shown = foreground or "unknown"
    return (
        text
        + ", but "
        + package
        + " is not in the foreground (foreground: "
        + shown
        + ")"
    )


# Verdict for an install/update goal, or None when nothing can be judged.
#
# pass iff the installed versionCode is >= the target (or > the previous one
# when there is no target) AND the app is in the foreground.
def auto_verify_install(facts: InstallFacts) -> Optional[Verdict]:
    judged = _judged_code(facts.previous_code, facts.installed_code, facts.target_code)
    if judged is None:
        return None
    ok, text = judged
    package = facts.package
    foreground = str(facts.foreground_package or "")
    if not ok:
        return Verdict(OUTCOME_FAIL, text, (text,))
    if foreground != package:
        reason = _install_reason(text, foreground, package)
        return Verdict(OUTCOME_FAIL, reason, (text,))
    return Verdict(
        OUTCOME_PASS, text + " and " + package + " is in the foreground", (text,)
    )
