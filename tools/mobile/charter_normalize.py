"""Normalisation: any object -> the typed, schema-only charter terms."""

from __future__ import annotations

from tools.mobile import explore_stop
from tools.mobile.charter_coerce import (
    _choice,
    _int,
    _items,
    _one_line,
    defaults_used,
)
from tools.mobile.charter_rejected import _rejected
from tools.mobile.charter_schema import (
    DEFAULTS_KEY,
    DEPTHS,
    DESTRUCTIVE,
    REJECTED_KEY,
    STOP_ON,
    defaults,
)


def _schema_only(raw: object) -> dict:
    """The SCHEMA keys of *raw* and NOTHING else. The ONE producer of the strip.

    Named rather than inlined so that it can be FOUND -- by a reader, and by a
    mutation harness. Mutant M2e is ``body = _schema_only(raw_body)`` ->
    ``body = raw_body``; the harness that tried to grade the inline dict
    comprehension this replaced could not locate it and reported neither a kill
    nor a gap. A strip nobody can anchor on is a strip nobody graded. M2e is
    RUN and KILLED. Anchor it on a line number or a code-only pattern: this
    docstring quotes the literal, and a harness that matches the PROSE mutates
    prose and reports a survival that is not evidence about the code.
    """
    body = raw if isinstance(raw, dict) else {}
    return {key: value for key, value in body.items() if key in defaults()}


# Any object -> the typed charter. TOTAL: it never raises and never fails.
#
# An unrecognised value for a closed field becomes that field's DEFAULT
# rather than an error, because a charter is the terms of a run and a run
# that refuses to start over a typo has helped nobody. What the tester did
# not set is recorded HERE, under :data:`DEFAULTS_KEY`, while the raw input
# is still in hand -- it cannot be recovered afterwards -- and that is what
# :func:`defaults_used` returns and the report names.
#
# **This function is NOT IDEMPOTENT on its own output, deliberately.**
# Re-normalising a charter this function produced strips :data:`DEFAULTS_KEY`
# along with every other non-schema key, and re-derives the verdict against a
# body in which every field is now filled -- so it yields an EMPTY list.
# Making it idempotent would require TRUSTING a :data:`DEFAULTS_KEY` found in
# the input, and the strip below cannot tell a verdict this module wrote from
# one a host sent; that is exactly the spoof route the strip exists to close.
# So the rule is stated rather than hidden: **every reader of the verdict
# must read it off the charter it was GIVEN, never off a fresh normalisation
# of it.** :func:`describe_terms` is the one reader that could get this
# wrong; it does it right, and mutant M11 (hand it the normalised copy)
# grades that -- a correctness property resting on an accident of call order
# is a property nobody can keep.
# STRIP the host's keys down to the schema BEFORE anything is derived from
# them. :data:`DEFAULTS_KEY` is the reason: it is this module's own OUTPUT
# channel and :func:`defaults_used` short-circuits on it, so a host that
# sends ``{"defaults_used": []}`` ERASED the disclosure sentence, and one
# that sends ``{"defaults_used": ["goal", "depth"]}`` FABRICATED defaults
# for fields the tester had set -- host control of constraint 2 in BOTH
# directions, demonstrated by executing the shipped code, not inferred.
# The intake packet's ``response_schema`` does NOT close this: a
# response_schema is a REQUEST TO A MODEL, not an enforcement boundary,
# and treating it as one is exactly how this reopened from the input side.
# The schema keys are the only keys; everything else, this one included,
# is dropped here, and the verdict recorded below is the SERVER's own.
def normalize(raw: object) -> dict:
    """Any object -> the typed charter. TOTAL: never raises."""
    raw_body = raw if isinstance(raw, dict) else {}
    body = _schema_only(raw_body)
    terms = _coerced_terms(body)
    terms[DEFAULTS_KEY] = defaults_used(body)
    terms[REJECTED_KEY] = _rejected(body)
    return terms


def _scope_terms(body: dict) -> dict:
    """The include/exclude lists of *body*'s scope, each bounded."""
    scope = body.get("scope")
    scope = scope if isinstance(scope, dict) else {}
    return {
        "include": _items(scope.get("include")),
        "exclude": _items(scope.get("exclude")),
    }


def _budget_terms(body: dict) -> dict:
    """The steps/minutes of *body*'s budget, non-negative integers."""
    budget = body.get("budget")
    budget = budget if isinstance(budget, dict) else {}
    return {
        "steps": max(0, _int(budget.get("steps"), 0)),
        "minutes": max(0, _int(budget.get("minutes"), 0)),
    }


# The goal's length bound belongs to the explore state, which is the thing
# that stores it.
def _coerced_terms(body: dict) -> dict:
    """The schema fields of an already schema-stripped *body*, each coerced."""
    terms = defaults()
    terms["goal"] = _one_line(body.get("goal"), explore_stop.MAX_GOAL_CHARS)
    terms["depth"] = _choice(body.get("depth"), DEPTHS, "both")
    terms["scope"] = _scope_terms(body)
    terms["destructive"] = _choice(body.get("destructive"), DESTRUCTIVE, "none")
    terms["budget"] = _budget_terms(body)
    terms["stop_on"] = _choice(body.get("stop_on"), STOP_ON, "budget")
    return terms
