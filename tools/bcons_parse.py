"""Parsing numeric ranges out of source text for ``bound_consistency``."""

from __future__ import annotations

import logging
import re

from tools.bcons_model import _Bound
from tools.bcons_text import _number, _ordered_tokens

logger = logging.getLogger("tools.bound_consistency")

_NUM = r"(\d[\d,]*(?:\.\d+)?)"

# "between SAR 100 and SAR 20,000", "from 1 to 50". The currency/unit token
# before the second number is optional and unconstrained in length only up to
# four characters, so "SAR"/"USD"/"kg" pass and a word does not.
_RANGE_RE = re.compile(
    r"\b(?:between|from)\b[^.;]{0,40}?"
    + _NUM
    + r"\s+(?:and|to)\s+[A-Za-z]{0,4}\s*"
    + _NUM,
    re.IGNORECASE,
)
_INCREMENT_RE = re.compile(r"increments?\s+of\s+[A-Za-z]{0,4}\s*" + _NUM, re.IGNORECASE)


def parse_bounds(grounding_text: str) -> list[_Bound]:
    """Numeric ranges stated in the SOURCE, each bound to its subject words.

    Whitespace is flattened before clauses are split: an acceptance criterion
    wraps across physical lines, and splitting on the newline separated
    "between SAR 100 and SAR 20,000" from the "in increments of SAR 100" that
    qualifies it -- which turned a correctly-rejected SAR 1,050 into a false
    positive. Never raises.
    """
    bounds: list[_Bound] = []
    try:
        flattened = " ".join((grounding_text or "").split())
        _parse_bounds_clauses(flattened, bounds)
        return _parse_bounds_resolve(bounds)
    except Exception:
        logger.exception("parse_bounds failed - returning what was parsed")
        return []


def _clause_range(clause: str) -> tuple[float, float, list[str]] | None:
    match = _RANGE_RE.search(clause)
    if not match:
        return None
    low, high = _number(match.group(1)), _number(match.group(2))
    if low >= high:
        return None
    ordered = _ordered_tokens(clause[: match.start()])
    if not ordered:
        return None
    return low, high, ordered


def _parse_bounds_clauses(flattened: str, bounds: list[_Bound]) -> None:
    """Append one raw bound for each clause of ``flattened`` that states a range."""
    for clause in re.split(r"[.;]", flattened):
        parsed = _clause_range(clause)
        if parsed is None:
            continue
        low, high, ordered = parsed
        increment = _INCREMENT_RE.search(clause)
        bounds.append(
            _Bound(
                low=low,
                high=high,
                increment=_number(increment.group(1)) if increment else None,
                subject=frozenset(ordered),
                distinctive=frozenset(ordered),
                order=tuple(ordered),
            )
        )


# A token two ranges share cannot tell them apart, so it is removed
# from both. What is left ({monthly, spending, cap} against
# {per-transaction, transaction, limit}) is what binding runs on.
def _parse_bounds_resolve(bounds: list[_Bound]) -> list[_Bound]:
    """Strip from each bound the subject tokens that another bound also has."""
    resolved: list[_Bound] = []
    for index, bound in enumerate(bounds):
        others: set[str] = set()
        for other_index, other in enumerate(bounds):
            if other_index != index:
                others |= other.subject
        resolved.append(
            _Bound(
                low=bound.low,
                high=bound.high,
                increment=bound.increment,
                subject=bound.subject,
                distinctive=frozenset(bound.subject - others),
                order=bound.order,
            )
        )
    return resolved
