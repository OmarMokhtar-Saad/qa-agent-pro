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
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

OUTCOME_PASS = "pass"
OUTCOME_FAIL = "fail"
OUTCOME_UNVERIFIED = "unverified"

#: Characters of a stop reason or refusal kept in a verdict line.
REASON_CHARS = 300

#: Characters of a refusal kept in the INFO log line.
DETAIL_CHARS = 160

RULE_TOTAL_WAIT = "total_wait"
RULE_ACTION_COUNT = "action_count"
RULE_VERDICT_WORD = "verdict_word"
RULE_NOT_JSON = "not_json"
RULE_EMPTY = "empty"
RULE_SCHEMA = "schema"

#: Lower-cased phrases of the real refusal texts in ``actions.py``, in the order
#: they are tried; anything else is a schema refusal.
_MARKERS = (
    (RULE_TOTAL_WAIT, ("waits in this script total",)),
    (RULE_ACTION_COUNT, ("one submission may carry at most",)),
    (RULE_VERDICT_WORD, ("done.verdict", "verdict: input should be")),
    (RULE_NOT_JSON, ("not valid json",)),
    (RULE_EMPTY, ("script was empty",)),
)


@dataclass(frozen=True)
class Verdict:
    """``outcome`` is pass / fail / unverified; ``reason`` is never empty."""

    outcome: str
    reason: str
    evidence: tuple = ()

    def line(self) -> str:
        return self.outcome + ": " + self.reason


def _clip(text: object, size: int) -> str:
    return " ".join(str(text or "").split())[:size]


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


def synthesize(
    stop_reason: str, last_refusal: str = "", trace_verdict: object = None
) -> Verdict:
    """The verdict for a run that stopped.

    A ``done`` verdict the run reached wins. Otherwise the run is
    ``unverified`` and the reason names the stop reason and, when there was
    one, the last refusal -- never a bare word.
    """
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


def _judged_code(
    previous: object, installed: object, target: object
) -> Optional[tuple]:
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


def auto_verify_install(
    *,
    previous_code: Optional[int],
    installed_code: Optional[int],
    target_code: Optional[int],
    foreground_package: str,
    package: str,
) -> Optional[Verdict]:
    """Verdict for an install/update goal, or None when nothing can be judged.

    pass iff the installed versionCode is >= the target (or > the previous one
    when there is no target) AND the app is in the foreground.
    """
    judged = _judged_code(previous_code, installed_code, target_code)
    if judged is None:
        return None
    ok, text = judged
    foreground = str(foreground_package or "")
    if not ok:
        return Verdict(OUTCOME_FAIL, text, (text,))
    if foreground != package:
        shown = foreground or "unknown"
        reason = (
            text
            + ", but "
            + package
            + " is not in the foreground (foreground: "
            + shown
            + ")"
        )
        return Verdict(OUTCOME_FAIL, reason, (text,))
    return Verdict(
        OUTCOME_PASS, text + " and " + package + " is in the foreground", (text,)
    )


def classify_refusal(error: object) -> str:
    """Which rule a script refusal text names (see ``_MARKERS``)."""
    low = " ".join(str(error or "").lower().split())
    for rule, needles in _MARKERS:
        if any(needle in low for needle in needles):
            return rule
    return RULE_SCHEMA


def rule_limit(rule: str) -> str:
    """The limit a rule enforces, read from the real constants."""
    from tools.mobile import actions

    if rule == RULE_TOTAL_WAIT:
        return "MAX_TOTAL_WAIT_MS=" + str(actions.MAX_TOTAL_WAIT_MS)
    if rule == RULE_ACTION_COUNT:
        return "MAX_MODEL_ACTIONS=" + str(actions.MAX_MODEL_ACTIONS)
    if rule == RULE_VERDICT_WORD:
        return "verdict in pass|fail|blocked"
    return ""


def log_refusal(error: object) -> str:
    """Write the one INFO line for a refused script and return it."""
    rule = classify_refusal(error)
    limit = rule_limit(rule)
    detail = ((limit + " ") if limit else "") + _clip(error, DETAIL_CHARS)
    line = "refused: " + rule + " " + detail
    logger.info("%s", line)
    return line
