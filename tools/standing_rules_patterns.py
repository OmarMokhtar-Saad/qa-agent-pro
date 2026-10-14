"""Compiled trigger patterns for the standing-rules detector (API / error flow / UI / questions)."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("tools.standing_rules")

# --- API triggers: strong (fire alone) vs weak (need a corroborating signal) ---

_API_STRONG_PATTERNS = (
    r"\bAPIs?\b",
    r"\bendpoints?\b",
    r"\bREST(?:ful)?\b",
    r"\bGraphQL\b",
    r"\bwebhooks?\b",
    r"\bmicroservices?\b",
    r"\brequest (?:body|payload|header|design)\b",
    r"\bresponse (?:body|payload|code|structure|schema)\b",
    r"\bstatus code\b",
    r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/",
    r"\bswagger\b",
    r"\bopenapi\b",
    r"\bbackend service\b",
    r"\bservice contract\b",
)
_API_STRONG_RE = tuple(re.compile(p, re.IGNORECASE) for p in _API_STRONG_PATTERNS)

# Each of these on its own is far too common on a pure-UI ticket. `\bHTTP\b`
# matches any `http://` link; `\bintegration\b` matches "integration with the
# wallet screen"; `\bJSON\b` matches a config snippet. TWO distinct weak hits are
# required before the API rules fire, and the result is marked weak-only.
_API_WEAK_PATTERNS = (
    r"\bHTTP\b",
    r"\bJSON\b",
    r"\bintegration\b",
    r"\bpayloads?\b",
    r"\bservice call\b",
    r"\bthird[- ]party\b",
)
_API_WEAK_RE = tuple(re.compile(p, re.IGNORECASE) for p in _API_WEAK_PATTERNS)
_API_WEAK_MIN = 2

# Error-flow signals. A bare 3-digit number is NOT one of them ("SAR 500",
# "500 users"): a status code must sit next to HTTP/status/error/response
# vocabulary. Bare "fail/failed/failure" is likewise excluded -- almost every
# negative test case says it -- so the flow must be named explicitly.
_ERROR_FLOW_PATTERNS = (
    r"\bAPI (?:failure|error)\b",
    r"\bservice (?:is )?(?:down|unavailable)\b",
    r"\btime[- ]?out(?:s|ed)?\b",
    r"\berror (?:flow|case|handling|state|response)\b",
    r"\b(?:HTTP|status|code|error|response|returns?|responds? with)\s*"
    r"(?:code)?\s*[:=]?\s*(?:4\d\d|5\d\d)\b",
    r"\b(?:4\d\d|5\d\d)\s+(?:error|response|status|page)\b",
    r"\bunhandled exception\b",
    r"\brollback\b",
)
_ERROR_FLOW_RE = tuple(re.compile(p, re.IGNORECASE) for p in _ERROR_FLOW_PATTERNS)

_UI_PATTERNS = (
    r"\bscreens?\b",
    r"\bpages?\b",
    r"\bUI\b",
    r"\bUX\b",
    r"\bbuttons?\b",
    r"\bforms?\b",
    r"\bfields?\b",
    r"\bmockups?\b",
    r"\bdesigns?\b",
    r"\bFigma\b",
    r"\blayout\b",
    r"\bdropdowns?\b",
    r"\bmodals?\b",
    r"\bbanner\b",
    r"\buser (?:can|sees|taps|clicks)\b",
    r"\bdisplay(?:s|ed)?\b",
)
_UI_RE = tuple(re.compile(p, re.IGNORECASE) for p in _UI_PATTERNS)

# An unresolved question in the ticket / comment thread. The generator must not
# silently pick a side ("should we return 201 or 202?" -> writes a 201 case as
# if it were documented); it is surfaced for clarification instead.
_OPEN_QUESTION_RE = re.compile(
    r"(?im)^.{0,200}?\b(?:should we|shall we|do we|can we|are we|which one|"
    r"still (?:open|tbd|to be (?:decided|confirmed))|to be (?:confirmed|decided)|"
    r"TBC|TBD|open question|pending (?:confirmation|decision)|waiting on)\b.{0,200}$"
)

_MAX_EVIDENCE = 5
_MAX_OPEN_QUESTIONS = 5
