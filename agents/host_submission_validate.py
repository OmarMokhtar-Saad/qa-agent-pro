"""Host mode: whole-suite validation with the salvage fallback."""

from __future__ import annotations

import logging

from agents.host_prep_serde import PrepParseError
from agents.host_submission_cases import _validate_cases
from agents.host_submission_dropped import _MAX_DROPPED_REASONS
from agents.host_submission_sidecar import (
    _parsed_submission,
    _pop_sidecar_fields,
    _Sidecar,
)
from agents.host_submission_types import ParsedSubmission
from config.settings_guard import never_raise
from tools.models import TestSuite

logger = logging.getLogger("agents.host_mode")


# Validate a candidate suite dict into a ParsedSubmission.
#
# Partial-validity policy: try whole-suite validation first (the common case,
# dropped_count == 0). If that fails, SALVAGE -- keep every individually-valid
# TestCase, drop the malformed ones (and any duplicate tc_id), and RECORD each
# drop. Rationale: a weak host almost always emits a few malformed cases;
# all-or-nothing would block the round trip forever, whereas keeping the valid
# ones lets each resubmission make progress. The dropped delta is returned (not
# swallowed) so ops-3d can ALWAYS tell the tester how many cases were discarded,
# regardless of checklist config. Raises PrepParseError only when
# NOTHING valid remains.
def _validate_suite(data: dict) -> ParsedSubmission:
    data, side = _pop_sidecar_fields(data)
    with never_raise(
        logger, "whole-suite validation failed; salvaging valid cases"
    ) as g:
        suite = TestSuite(**data)
    if not g.failed:
        return _parsed_submission(suite, side)

    return _salvage_suite(data, side)


def _salvage_suite(data: dict, side: _Sidecar) -> ParsedSubmission:
    valid, dropped, salvaged = _validate_cases(data.get("test_cases"))
    with never_raise() as g:
        suite = TestSuite(test_cases=valid)
    if g.failed:
        raise PrepParseError(f"could not assemble a valid suite: {g.exc}") from g.exc
    drops = {
        "dropped_count": len(dropped),
        "dropped_reasons": dropped[:_MAX_DROPPED_REASONS],
        "salvaged_reasons": salvaged[:_MAX_DROPPED_REASONS],
    }
    return _parsed_submission(suite, side, drops)
