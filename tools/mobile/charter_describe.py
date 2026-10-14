"""The rejection disclosure and the whole terms sentence."""

from __future__ import annotations

from tools.mobile.charter_clauses import (
    _budget_clause,
    _charter_clause,
    _defaults_clause,
    _stop_clauses,
)
from tools.mobile.charter_coerce import defaults_used
from tools.mobile.charter_normalize import normalize
from tools.mobile.charter_rejected import rejected_fields


def _rejected_clause(charter: object) -> str:
    """The disclosure sentence for every out-of-enum answer this run replaced,
    or ``""``. Extracted from :func:`describe_terms` so a caller that only
    needs THIS clause -- before the run has any cases, let alone a finished
    report -- can get it without paying for the rest of the sentence. See
    :func:`rejection_notice`.
    """
    said = ""
    for spoiled in rejected_fields(charter):
        said += (
            str(spoiled.get("field"))
            + " was sent as "
            + str(spoiled.get("sent"))
            + ", which is not one of "
            + ", ".join(str(choice) for choice in (spoiled.get("choices") or []))
            + ", so the server used "
            + str(spoiled.get("used"))
            + ". "
        )
    return said


def rejection_notice(charter: object) -> str:
    """The SAME disclosure :func:`describe_terms` buries in the finished
    report, surfaced at the START -- an unknown enum value used to be named
    only once the run was already over, never in `qa_mobile_test`'s own
    immediate reply. ``""`` when nothing was rejected.
    """
    return _rejected_clause(charter)


# The run's TERMS as one plain sentence, defaults NAMED. ONE producer.
#
# Returns ``""`` for a run with no charter -- every run recorded before this
# existed -- so the page it goes on renders exactly as it did.
#
# **Order is load-bearing.** The report clips this string, and the scope can
# fill it on its own (``MAX_SCOPE_ITEMS`` x ``MAX_SCOPE_ITEM_CHARS``), so
# every clause that DISCLOSES something -- which defaults were assumed, which
# stop condition is not detected -- comes AHEAD of the clippable content
# it concerns. A disclosure that a long scope can push past the clip is a
# disclosure that is not made.
#
# Ordering alone does not deliver that, and the difference matters: the
# charter clause sits AFTER the budget clause, so it survives the clip only
# because every clause ahead of it is BOUNDED. The scope is
# bounded by ``MAX_SCOPE_ITEMS``/``MAX_SCOPE_ITEM_CHARS`` and the budget
# figures by :data:`MAX_BUDGET_UNITS` in :func:`_int` -- without which a
# caller sending ``1e300`` writes a 301-digit number into that clause and
# pushes the disclosure off the page (mutant M12). ONE unbounded clause
# anywhere ahead of a disclosure defeats the whole ordering, so a new clause
# owes a bound.
#
# **Voice is load-bearing too, and it follows the WIRING.** ``depth``,
# ``scope`` and ``destructive`` now steer the run, so they are rendered in
# the present indicative and the sentence says a resume explores under the
# same terms. What is STILL unwired -- ``coverage_plateau`` -- keeps the
# recorded-but-not-yet-detected voice. A disclosure and the pins that grade
# it are ONE artifact: changing this text without moving those pins is how a
# stale claim survives its own fix.
#
# The defaults clause is not decoration. A default nobody is told about is a
# run whose terms cannot be reconstructed afterwards, which is the same
# failure as not recording them at all.
# describe_terms notes (kept beside the function, out of its body):
# :func:`normalize` is the strip point, so a charter that reached
# persistence carries the SERVER's verdict and never a host-supplied list.
# READ OFF ``charter``, THE ARGUMENT AS GIVEN -- never off ``terms``.
# :func:`normalize` is NOT idempotent on its own output (see its
# docstring): re-normalising strips ``DEFAULTS_KEY`` and re-derives it
# against a fully filled body, so ``defaults_used(terms)`` is ALWAYS [] and
# the disclosure clause silently disappears. That is mutant M11, and it is
# named here because this line's correctness otherwise rests on an accident
# of call order that no reader could be expected to preserve.
# REFUSE BY NAME, AHEAD OF EVERY CLIPPABLE CLAUSE. A coercer that replaces
# an out-of-enum answer with the default REDUCES what was asked for, and
# the invariant is that such a site refuses by name in the same reply. Read
# off ``charter``, the argument as given, for the M11 reason: re-normalising
# strips the recorded verdict. The clause is BOUNDED -- at most five
# entries, each value trimmed to 40 characters -- because one unbounded
# clause ahead of a disclosure defeats the whole ordering. See
# :func:`_rejected_clause`, the same loop extracted so `rejection_notice`
# can surface it before this run has a finished report.
def describe_terms(charter: object) -> str:
    """The run's TERMS as one plain sentence, defaults NAMED. ONE producer."""
    if not isinstance(charter, dict) or not charter:
        return ""
    terms = normalize(charter)
    return (
        _rejected_clause(charter)
        + _defaults_clause(defaults_used(charter))
        + _stop_clauses(terms)
        + _budget_clause(terms)
        + _charter_clause(terms)
    )
