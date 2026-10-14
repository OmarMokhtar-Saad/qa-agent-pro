"""Numeric-bound contradictions between a case and its source (finding F11).

`suite_consistency` asks whether an expected result has a *shape* that can
fail. This module asks a different question: given that the source states a
numeric range for a field, does a case enter a value that the range makes
LEGAL and then assert it is rejected -- or enter an ILLEGAL one and assert it
is accepted? Either way the case fails against correct software, and no other
check in the pipeline reads a number.

On the 2026-08-16 run it reports ``TC-094``: the source allows a monthly
spending cap "between SAR 1,000 and SAR 200,000", the case enters 99,999 --
inside that range -- and asserts the Save button is disabled.

The whole difficulty is BINDING a range to the right field. Getting it wrong
would flag the eight correct Boundary-Values cases in that same suite, which
is a far worse outcome than missing one bad case, so every rule here is
biased towards silence:

* a step binds to a range only when it shares at least
  ``_MIN_SUBJECT_OVERLAP`` DISTINCTIVE subject tokens with exactly one range;
  a tie or a weaker match is dropped rather than guessed at;
* a value is read only after an enter/set verb, and only when it is a wholly
  numeric quoted literal or is currency-prefixed -- a bare integer in an
  action is far more often a card number, an id or a purchase amount
  (``Set credit 8834 per-transaction limit to SAR 2,000``);
* a stated increment ("in increments of SAR 100") makes an in-range value
  illegal, so a case that rejects SAR 1,050 is consistent, not a finding;
* an expected result that reads as both accepting and rejecting, or as
  neither, yields nothing.

Replayed over 5,098 cases from 63 stored suites this emits exactly one
finding, the true positive above.

Scope, deliberately: it does NOT report that ``TC-094`` is tagged ``AC-003``
while testing AC-004's field, even though the binding knows it -- the
requirement-id tag is finding F04's deliverable, and one advisory should not
be emitted twice. Nor does it touch the disjunctive oracle in the same
expected result, which is F02's.

Deterministic, bounded, model-free, and -- like every other module in
``tools/`` -- never raises to callers.
"""

from __future__ import annotations

from tools.bcons_find import find_bound_contradictions  # noqa: F401
from tools.bcons_model import BoundFinding  # noqa: F401
from tools.bcons_parse import parse_bounds  # noqa: F401
from tools.bcons_value import entered_value  # noqa: F401

__all__ = [
    "BoundFinding",
    "entered_value",
    "find_bound_contradictions",
    "parse_bounds",
]
