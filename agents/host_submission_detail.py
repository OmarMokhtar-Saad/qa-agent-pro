"""Host mode: the per-case validation detail text of a rejected submitted case."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")

_MAX_FIELD_ERRORS_PER_CASE = 20


def _error_parts(errors) -> list[str]:
    parts = []
    for err in list(errors)[:_MAX_FIELD_ERRORS_PER_CASE]:
        loc = ".".join(str(p) for p in (err.get("loc") or ())) or "(root)"
        msg = str(err.get("msg") or "invalid")[:120]
        parts.append(f"field `{loc}`: {msg}")
    return parts


def _more_errors_suffix(total: int) -> str:
    if total <= _MAX_FIELD_ERRORS_PER_CASE:
        return ""
    return f", +{total - _MAX_FIELD_ERRORS_PER_CASE} more"


# ``field `tc_id`: <rule>`` for every failing field, up to
# _MAX_FIELD_ERRORS_PER_CASE (v1.97.0 cursor-hardening item 4b -- a case
# invalid on 5 fields at once used to report 2 and hide the rest behind a
# bare "+N more" count).
#
# Falls back to the exception class name for anything that is not a pydantic
# ValidationError, which is exactly the previous behaviour. Never raises, and
# never echoes the rejected value.
# The fallback below is the documented behaviour for a non-pydantic
# error, so this is not a failure path -- but a pydantic error whose
# .errors() raises IS one, and it used to be indistinguishable.
def _validation_detail(exc: Exception) -> str:
    with never_raise(
        logger, "host_mode: could not read validation errors", exc_info=True
    ):
        errors = exc.errors()  # type: ignore[attr-defined]
        parts = _error_parts(errors)
        if parts:
            return "; ".join(parts) + _more_errors_suffix(len(errors))
    return type(exc).__name__
