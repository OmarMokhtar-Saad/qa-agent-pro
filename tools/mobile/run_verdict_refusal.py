"""The refusal half of the verdict module: rule names, classifier and the INFO log line.

Every script refusal writes one INFO line ``refused: <rule> <detail>`` so a
refusal that exists only in a chat transcript is in the server log the next
time. Pure: no disk, no device, no model.
"""

from __future__ import annotations

import logging

from tools.mobile import actions, dump_reuse

logger = logging.getLogger("tools.mobile.run_verdict")

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


def _clip(text: object, size: int) -> str:
    return " ".join(str(text or "").split())[:size]


def classify_refusal(error: object) -> str:
    """Which rule a script refusal text names (see ``_MARKERS``)."""
    low = " ".join(str(error or "").lower().split())
    for rule, needles in _MARKERS:
        if any(needle in low for needle in needles):
            return rule
    return RULE_SCHEMA


def rule_limit(rule: str) -> str:
    """The limit a rule enforces, read from the real constants."""

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
    dump_reuse.note_refusal()
    return line
