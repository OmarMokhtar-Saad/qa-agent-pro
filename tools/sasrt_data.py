"""Echoed test_data detection for step_assertion (see tools/step_assertion.py)."""

from __future__ import annotations

import logging

from tools.sasrt_text import _ALNUM_RE, _get
from tools.sasrt_types import DataFinding

logger = logging.getLogger("tools.step_assertion")


def _echo(tc_id: str, datum: object) -> DataFinding | None:
    """The finding for one test_data entry, or None when its value adds content."""
    field = str(_get(datum, "field", "") or "")
    value = str(_get(datum, "example_value", "") or "")
    if not field:
        return None
    field_tokens = [w.lower() for w in _ALNUM_RE.findall(field)]
    value_tokens = [w.lower() for w in _ALNUM_RE.findall(value)]
    if not (field_tokens and field_tokens == value_tokens):
        return None
    return DataFinding(
        tc_id=tc_id,
        field=field[:80],
        example_value=value[:80],
        kind="echoes-field-name",
    )


# test_data entries whose value merely restates the field name.
#
# DISCLOSED ONLY -- this never refuses a submission.
#
# Measured on the TICKET-5692 run: 42 of 133 fields echo their own name
# (``simulated_payment_gateway_failure`` -> "Simulated payment gateway
# failure"). The comparison is on ALPHANUMERIC tokens, so case, underscores
# and punctuation are ignored and a value that adds ANY real content is
# silent.
def find_echoed_test_data(cases: list) -> list[DataFinding]:
    """test_data entries whose value merely restates the field name."""
    findings: list[DataFinding] = []
    try:
        for case in cases or []:
            tc_id = str(_get(case, "tc_id", "") or "")
            for datum in _get(case, "test_data", None) or []:
                found = _echo(tc_id, datum)
                if found is not None:
                    findings.append(found)
    except Exception:
        logger.exception("find_echoed_test_data failed - returning what was found")
    return findings
