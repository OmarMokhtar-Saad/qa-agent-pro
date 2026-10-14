"""Mandated checklist lines (SR-API-1..4, SR-UI-1) for the standing rules."""

from __future__ import annotations

import logging

from tools.standing_rules_detect import Triggers
from tools.standing_rules_text import ASSUMED_MARKER

logger = logging.getLogger("tools.standing_rules")

_Line = tuple[str, str, str]

_SR_API_4: _Line = (
    "SR-API-4",
    "If the failure flow the story names occurs, then the system "
    "shall return a structured error (code + message), leave "
    "nothing half-committed, and behave predictably on retry.",
    "backend",
)

_SR_UI_1: _Line = (
    "SR-UI-1",
    "The system shall render the feature's main screen to baseline "
    "build quality: every element from the design present and "
    "correctly spelled, alignment and spacing matching the mockup, "
    "nothing truncated or overlapping at the default window/device "
    "size, interactive elements showing their hover/focus/disabled "
    "states, and the screen still correct at the smallest supported "
    "width.",
    "ui",
)


def _api_contract_source(has_spec: bool) -> str:
    """Where the API checklist lines say field names and codes come from."""
    if has_spec:
        return "the linked OpenAPI/Swagger spec"
    return (
        "standard REST convention (ASSUMED - the ticket documents no "
        "contract, so the case title must start with the literal marker "
        + ASSUMED_MARKER
        + ")"
    )


def _sr_api_1(source: str) -> _Line:
    return (
        "SR-API-1",
        "The system shall return the correct STATUS CODES for both a "
        "successful call and every error path the story implies (2xx on "
        "success; 4xx for bad input, missing auth or a missing resource; "
        "5xx surfaced as a user-visible failure, not a blank screen), "
        f"asserting the exact numeric code per {source}.",
        "backend",
    )


def _sr_api_2(source: str) -> _Line:
    return (
        "SR-API-2",
        "The system shall accept the documented REQUEST DESIGN -- "
        "required headers (content type, authorization), the request body "
        "shape, required vs. optional fields -- and shall reject a "
        "request that omits a required field. Field names and types per "
        f"{source}.",
        "backend",
    )


def _sr_api_3(source: str) -> _Line:
    return (
        "SR-API-3",
        "The system shall return the documented RESPONSE STRUCTURE: every "
        "field present, of the right type and nested as specified, with "
        "unexpected nulls or missing fields treated as failures. Field "
        f"names and types per {source}.",
        "backend",
    )


def _checklist_section(triggers: Triggers) -> list[_Line]:
    """The API checklist lines (SR-API-1..4) for a fired API trigger."""
    source = _api_contract_source(triggers.has_spec)
    out = [_sr_api_1(source), _sr_api_2(source), _sr_api_3(source)]
    if triggers.error_flow:
        out.append(_SR_API_4)
    return out


# Three API lines always fire when the API trigger fires (status codes for
# success AND error, request design, response structure); a fourth error
# handling line is added only when the ticket actually names a failure flow, so
# a passing "API" mention does not inflate the checklist. One baseline UI line
# fires for any user-facing story. Written in EARS ubiquitous shape to match
# the rest of the Batch-2 checklist.
def standing_checklist_lines(triggers: Triggers) -> list[tuple[str, str, str]]:
    """Mandated checklist lines as ``(line_id, text, subsystem)`` tuples. Never raises."""
    out: list[tuple[str, str, str]] = []
    try:
        if triggers.api:
            out.extend(_checklist_section(triggers))
        if triggers.ui:
            out.append(_SR_UI_1)
    except Exception:
        logger.exception("standing_checklist_lines failed - returning partial result")
    return out
