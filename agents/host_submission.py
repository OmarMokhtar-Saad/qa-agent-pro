"""Host mode: submitted-suite parsing, salvage and validation. Facade over agents/host_submission_* (C5 w14)."""

from __future__ import annotations

from agents.host_submission_bounds import (
    _MAX_JSON_DEPTH,  # noqa: F401
    _check_submitted_object,  # noqa: F401
)
from agents.host_submission_detail import (
    _MAX_FIELD_ERRORS_PER_CASE,  # noqa: F401
    _validation_detail,  # noqa: F401
)
from agents.host_submission_dropped import _MAX_DROPPED_REASONS  # noqa: F401
from agents.host_submission_parse import (  # noqa: F401
    _strip_json_fence,
    parse_host_suite,
)
from agents.host_submission_prepid import safe_prep_id  # noqa: F401
from agents.host_submission_scan import (  # noqa: F401
    _bounded_json_spans,
    _largest_suite_object,
)
from agents.host_submission_types import ParsedSubmission  # noqa: F401
from agents.host_submission_validate import _validate_suite  # noqa: F401
