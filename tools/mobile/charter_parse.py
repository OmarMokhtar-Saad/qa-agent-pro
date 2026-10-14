"""Parsing the host-submitted charter string into normalised terms."""

from __future__ import annotations

import json

from tools.mobile.charter_consts import MAX_CHARTER_BYTES
from tools.mobile.charter_normalize import _schema_only, normalize
from tools.mobile.charter_rejected import _refusal_message, _rejected


def _oversize_refusal() -> dict:
    """The refusal for a charter over the byte cap."""
    return {
        "error": (
            "That charter is larger than this server will read ("
            + str(MAX_CHARTER_BYTES)
            + " bytes). Nothing was started -- send the goal, the "
            "depth, the scope, the destructive setting and the budget, "
            "and leave the rest out."
        ),
        "content": None,
    }


def _unreadable_refusal(exc: Exception) -> dict:
    """The refusal for a charter that is not JSON."""
    return {
        "error": (
            "That charter is not readable as JSON ("
            + str(exc)[:120]
            + "). Nothing was started."
        ),
        "content": None,
    }


def _decode_charter(text: object) -> tuple:
    """The submitted string -> ``(body, failure)``; *failure* is the refusal or None."""
    raw = text if isinstance(text, str) else ""
    if not raw.strip():
        return {}, None
    if len(raw.encode("utf-8", "ignore")) > MAX_CHARTER_BYTES:
        return None, _oversize_refusal()
    try:
        return json.loads(raw), None
    except (TypeError, ValueError, OverflowError) as exc:
        return None, _unreadable_refusal(exc)


# The HOST-SUBMITTED charter string -> ``{"error", "content", "raw"}``.
#
# Treated as untrusted input, the shape ``tools/jira_mcp.py`` uses: size
# capped, ``json.loads`` only -- never ``eval``, never a literal parser --
# and every field coerced by :func:`normalize`.
#
# *goal* pre-answers the first question when the caller already has one, so a
# start that carries ``goal=`` runs under the default charter instead of
# being sent back for an intake it does not need.
# THE RAW (schema-stripped) BODY TRAVELS WITH THE TERMS, under ``raw``.
# :func:`questions_for` must judge what the tester actually SENT:
# normalisation fills every field, so a NORMALISED charter reads as four
# of five ANSWERED and the intake asks ONE question while silently
# defaulting the rest -- measured, and the reason this key exists. The two
# keys are not interchangeable: ``content`` is the run's terms, ``raw`` is
# the evidence of what was answered.
def parse(text: object, goal: str = "") -> dict:
    """The HOST-SUBMITTED charter string -> {error, content, raw}."""
    body, failure = _decode_charter(text)
    if failure is not None:
        return failure
    supplied = _schema_only(body)
    rejected = _rejected(supplied)
    if rejected:
        return {"error": _refusal_message(rejected), "content": None}
    terms = normalize(supplied)
    if not terms["goal"]:
        supplied["goal"] = goal
        terms = normalize(supplied)
    return {"error": None, "content": terms, "raw": supplied}
