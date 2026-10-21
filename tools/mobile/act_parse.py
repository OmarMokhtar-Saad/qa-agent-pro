"""Parsing a model script: the queue fold, the refusals and the validated Script."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from tools.mobile import script_help
from tools.mobile.action_consts import MAX_ACTIONS, MAX_ERROR_CHARS, MAX_MODEL_ACTIONS
from tools.mobile.action_schema import Script

logger = logging.getLogger("tools.mobile.actions")


# Actions left queued by a budget stop, folded with a fresh submit.
#
# Queued actions run FIRST -- they are unreached work from a script the
# model already committed to, not a second draft -- and the combined total
# is held to the SAME ceiling a single script is: ``MAX_ACTIONS``. Over
# that cap this refuses BY NAME rather than silently truncating either
# side, because the model chose what to resend without knowing the exact
# queue depth, and dropping part of either list would replay a script the
# model believes is intact.
#
# Shared by both mobile lanes: ``session._combine_queued_actions``
# (EXPLORE) delegates here, and ``case_runner.submit_case`` (SCRIPTED)
# calls it directly, because ``case_runner`` cannot import ``session``
# (``session`` already imports ``case_runner``).
def combine_queued_actions(queued: Any, new_actions: Any) -> dict:
    """Actions left queued by a budget stop, folded with a fresh submit."""
    queued_list = list(queued or [])
    new_list = list(new_actions or [])
    combined = queued_list + new_list
    # The NEW part is a model submission and is held to MAX_MODEL_ACTIONS; the
    # queued part was accepted earlier and may be longer.
    if len(new_list) > MAX_MODEL_ACTIONS:
        return {"ok": False, "reason": too_many_actions(len(new_list))}
    if len(combined) > MAX_ACTIONS:
        return {
            "ok": False,
            "reason": _over_cap_reason(queued_list, new_list, combined),
        }
    return {"ok": True, "actions": combined}


def _over_cap_reason(queued_list: list, new_list: list, combined: list) -> str:
    """The by-name refusal for a queue plus a submit over the combined cap."""
    return (
        str(len(queued_list))
        + " action(s) are already queued from a prior budget stop and "
        + str(len(new_list))
        + " more were just submitted -- "
        + str(len(combined))
        + " total, over the "
        + str(MAX_ACTIONS)
        + "-action limit. Resend fewer new actions, or none: the "
        "queued ones will still run."
    )


def too_many_actions(count: int, limit: int = MAX_MODEL_ACTIONS) -> str:
    """The refusal for a submission carrying more than *limit* actions."""
    return (
        "This script was refused and nothing was replayed. It has "
        + str(count)
        + " actions; one submission may carry at most "
        + str(limit)
        + ". Send the first steps, read the screen that comes back, then send "
        "the next ones."
    )


def _refused(message: str) -> dict:
    """A refusal that teaches: the message, then the op format (script_help)."""
    return {"error": message + "\n" + script_help.generic_help(), "content": None}


def _validation_refusal(exc: ValidationError, payload: object) -> dict:
    """A schema refusal: up to five problems (cut at MAX_ERROR_CHARS), then the
    failing op's fragment and a corrected example from script_help."""
    errors = exc.errors()
    problems = []
    for problem in errors[:5]:
        where = ".".join(str(part) for part in problem.get("loc") or ())
        problems.append((where or "actions") + ": " + str(problem.get("msg") or ""))
    head = ("This script was refused and nothing was replayed. " + "; ".join(problems))[
        :MAX_ERROR_CHARS
    ]
    return {
        "error": head + "\n" + script_help.refusal_help(errors, payload),
        "content": None,
    }


# A model's reply -> a validated :class:`Script`. Never raises.
#
# Accepts the packet shape (``{"actions": [...]}``), a bare list, or a JSON
# string of either -- a chat model reliably produces all three and refusing
# two of them would boomerang a correct plan.
#
# *max_actions* defaults to :data:`MAX_MODEL_ACTIONS` because a caller that
# passes nothing is parsing a fresh model submission. A caller parsing
# actions that were already accepted -- a budget-stop queue folded with a new
# submit, which :func:`combine_queued_actions` has already held to both caps
# -- passes :data:`MAX_ACTIONS`.
def parse_script(raw: object, *, max_actions: int = MAX_MODEL_ACTIONS) -> dict:
    """A model's reply -> a validated :class:`Script`. Never raises."""
    try:
        payload, refusal = _payload_or_refusal(raw)
        if refusal is not None:
            return refusal
        return _validated(payload, max_actions)
    except ValidationError as exc:
        return _validation_refusal(exc, payload)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.actions.parse_script failed")
        return {"error": str(exc), "content": None}


def _payload_or_refusal(raw: object) -> tuple[Any, dict | None]:
    """The reply as a dict payload, or a refusal when it cannot be one."""
    payload = raw
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        payload, refusal = _from_text(payload)
        if refusal is not None:
            return payload, refusal
    if isinstance(payload, list):
        payload = {"actions": payload}
    if not isinstance(payload, dict):
        return payload, _refused(
            "The script must be a JSON object with an `actions` list."
        )
    return payload, None


def _not_json_text(exc: json.JSONDecodeError) -> str:
    """The refusal text; a parse error says where (line and column) and why."""
    line, col = getattr(exc, "lineno", 0), getattr(exc, "colno", 0)
    where = " (line %d, column %d: %s)" % (line, col, exc.msg) if line else ""
    return "The script was not valid JSON%s and was not replayed." % where


def _from_text(payload: str) -> tuple[object, dict | None]:
    """The JSON a reply string holds, or a refusal when it is empty or not JSON."""
    text = payload.strip()
    if not text:
        return payload, _refused("The script was empty.")
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return payload, _refused(_not_json_text(exc))


def _validated(payload: dict, max_actions: int) -> dict:
    """The Script for a payload within the cap, or the refusal that names why not.

    Raises ValidationError for a payload that is not a Script; the caller refuses it.
    """
    listed = payload.get("actions")
    if isinstance(listed, list) and len(listed) > max_actions:
        return {"error": too_many_actions(len(listed), max_actions), "content": None}
    script = Script.model_validate(payload)
    return {"error": None, "content": script}
