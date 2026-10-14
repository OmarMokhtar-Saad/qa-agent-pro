"""Standing rules trigger detector (Batch 3 / rule pack 3).

Two domain rules that fire on TICKET CONTENT rather than on a fan-out category:

* ANY mention of an API -- even a passing "API Failure" error flow with no
  contract attached -- mandates status-code, request-design and
  response-structure coverage (plus error handling when the ticket names a
  failure flow). Today Integration/Security exist only as fan-out CATEGORIES,
  which the model is free to fill with UI cases.
* EVERY user-facing story gets one baseline "the UI is built properly" case.

TRIGGER PRECISION IS A REAL RISK, so the API detector is TWO-TIERED:

  STRONG signals (``_API_STRONG``) each fire on their own: "API", "endpoint",
  "REST", "GraphQL", "webhook", "microservice", "request body", "response
  structure", "status code", "GET /path", "swagger", "openapi", "backend
  service".

  WEAK signals (``_API_WEAK``) -- "HTTP" (which also matches a bare
  ``http://`` URL), "JSON", "integration", "payload", "service call" -- do NOT
  fire alone. TWO DISTINCT weak signals are required, and the result is recorded
  as ``api_weak_only`` so both the prompt block and the advisory section say the
  trigger was circumstantial and name the exact words that fired it. Without
  this, a pure-UI ticket saying "integration with the wallet screen" forced
  three or four mandatory backend API cases onto the generator.

  Bare 3-digit numbers are NOT an error-flow signal either: ``\\b(?:4\\d\\d|5\\d\\d)\\b``
  matched "SAR 500". The status-code pattern now requires adjacent HTTP/status
  vocabulary.

Where the ticket documents no contract, the resulting cases are written against
standard REST convention and MECHANICALLY labelled as assumed. The label is a
fixed constant plus the sanitised source reference -- never an LLM-written
citation, so it can never turn into "per RFC 9110" for an RFC nobody cited. When
a real OpenAPI/Swagger spec was fetched (``tools/swagger_fetcher``), the
assumption disappears and the cases are documented instead.

Never raises.
"""

from __future__ import annotations

import logging
import re  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401

from tools.models import TestCase  # noqa: F401
from tools.standing_rules_checklist import standing_checklist_lines  # noqa: F401
from tools.standing_rules_detect import (  # noqa: F401
    TriggerContext,
    Triggers,
    detect_triggers,
)
from tools.standing_rules_notes import (  # noqa: F401
    ContractBasis,
    annotate_assumed_cases,
)
from tools.standing_rules_patterns import (  # noqa: F401
    _API_WEAK_MIN,
    _MAX_EVIDENCE,
    _MAX_OPEN_QUESTIONS,
)
from tools.standing_rules_prompt import format_standing_prompt_block  # noqa: F401
from tools.standing_rules_text import (  # noqa: F401
    _MAX_QUESTION_CHARS,
    _MAX_REF_CHARS,
    ASSUMED_AR,
    ASSUMED_EN,
    ASSUMED_MARKER,
    CLARIFY_MARKER,
    DOCUMENTED_EN,
    assumed_label,
    safe_display,
    safe_ref,
)
from tools.standing_rules_warning import standing_warning_section  # noqa: F401
from tools.swagger_fetcher import looks_like_openapi_url  # noqa: F401

logger = logging.getLogger(__name__)
