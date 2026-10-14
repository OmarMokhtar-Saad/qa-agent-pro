"""Host mode: parse_host_suite, the tolerant entry point for a submitted suite."""

from __future__ import annotations

import re

from agents.host_prep_serde import PrepParseError
from agents.host_submission_bounds import (
    _MAX_JSON_DEPTH,
    _check_submitted_object,
    _check_text_size_cap,
    _text_nesting_depth,
)
from agents.host_submission_scan import _largest_suite_object
from agents.host_submission_types import ParsedSubmission
from agents.host_submission_validate import _validate_suite


# Tolerantly extract the host's generated suite into a ParsedSubmission
# (suite + salvage delta).
#
# Accepts an already-parsed dict, a bare JSON string, a ```json fenced block, or
# prose-wrapped JSON with chatter and multiple candidate objects. Among several
# top-level balanced objects the LARGEST one carrying a ``test_cases`` key wins
# -- a chat model frequently echoes a small schema/example object BEFORE the
# real suite, so "first object" would pick the wrong one.
#
# *enforce_size_cap* is True for everything a HOST submits. It is False
# for the merged dict the SERVER itself builds out of already-capped
# per-category rows on the Path-A finalize: applying QA_PREP_MAX_BYTES to
# that concatenation refused suites nobody had over-submitted (F7).
#
# UNTRUSTED-safe: host output re-enters the server, so there is NO code
# execution -- only ``json.loads``, never eval or ast-based literal parsing.
# Work is PROVABLY bounded: the input is rejected above
# ``settings.qa_prep_max_bytes`` before any scan, and extraction uses the LOCAL
# single-pass O(n) ``_bounded_json_spans`` (NOT llm._balanced_json_spans, which
# is O(n^2) on hostile input), so a pathological unbalanced-brace blob is
# rejected fast instead of hanging. Raises PrepParseError so ops-3d can turn a
# bad submission into a tester-readable message.
def parse_host_suite(text_or_obj, *, enforce_size_cap: bool = True) -> ParsedSubmission:
    if isinstance(text_or_obj, dict):
        _check_submitted_object(text_or_obj, enforce_size_cap)
        return _validate_suite(text_or_obj)
    if not isinstance(text_or_obj, str):
        raise PrepParseError(
            "host suite must be a JSON string or object, got "
            f"{type(text_or_obj).__name__}"
        )

    _check_text_size_cap(text_or_obj, enforce_size_cap)
    return _parse_suite_text(text_or_obj)


def _parse_suite_text(text: str) -> ParsedSubmission:
    """Fence-strip, depth-check and validate the largest suite object in ``text``."""
    stripped = _strip_json_fence(text.strip())

    if _text_nesting_depth(stripped) > _MAX_JSON_DEPTH:
        raise PrepParseError(
            f"submitted JSON nests deeper than {_MAX_JSON_DEPTH} levels"
        )

    best = _largest_suite_object(stripped)

    if best is None:
        raise PrepParseError(
            "no JSON object with a 'test_cases' key found in the submitted text"
        )
    return _validate_suite(best)


# 2026-09-02 audit F10b. This was re.sub(r"\s*```$", "", stripped).
# An unbounded whitespace run in front of an ANCHORED literal makes the
# REFUSAL path quadratic: the engine restarts `\s*` at every position of
# the run and each restart walks to the end of it. A fenced payload
# whose JSON carries one long internal whitespace run and no closing
# fence therefore cost x4 per doubling -- 40,000 spaces measured 2.7 s,
# on EVERY qa_submit_suite / qa_submit_category.
#
# rstrip + endswith is the same transformation in one linear pass. It is
# EQUIVALENT, not merely close: `$` could only ever match at the end of
# this string, because the .strip() above has already removed any
# trailing newline, and `\s*` could only ever consume the whitespace
# immediately before the closing fence.
def _strip_json_fence(stripped: str) -> str:
    """Remove a leading ```json fence and a trailing ``` fence, if present."""
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = stripped.rstrip()
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    return stripped
