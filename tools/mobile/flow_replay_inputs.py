"""Tester input names to flow parameter names, for any app.

A tester may call an input ``password`` where the saved flow says ``login_password``.
An input stands in for a name the flow needs when their word sets are equal or one
holds the other's (``otp`` in ``login_otp``), and only when exactly one input fits.
"""

from __future__ import annotations

import re

from tools.mobile.app_flows_check import secret_fields

_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


def tokens(name: str) -> frozenset:
    """The lowercase words of *name* (split on non-alphanumerics and camelCase)."""
    return frozenset(w.lower() for w in _WORDS.findall(str(name)))


def _fits(want: frozenset, have: frozenset) -> bool:
    return bool(want) and bool(have) and (want <= have or have <= want)


def wanted(template: dict) -> list:
    """Every input name the flow needs: its parameters, then its secret fields."""
    declared = [str(p) for p in template.get("params") or []]
    return list(dict.fromkeys(declared + secret_fields(template)))


def aliases(template: dict, inputs: dict) -> dict:
    """``{needed name: input name}`` for each absent need that exactly one input fits.

    An input that already names a need is never renamed, and an input that fits two
    needs serves neither, so one secret never fills two fields.
    """
    needs = wanted(template)
    spare = [k for k in inputs if k not in needs]
    fit = {
        n: [k for k in spare if _fits(tokens(n), tokens(k))]
        for n in needs
        if n not in inputs
    }
    uses = [k for hits in fit.values() for k in hits]
    return {n: h[0] for n, h in fit.items() if len(h) == 1 and uses.count(h[0]) == 1}


def resolve(template: dict, inputs: dict) -> dict:
    """*inputs* plus each alias the flow needs (an exact name always wins)."""
    found = aliases(template, inputs)
    return {**{n: inputs[k] for n, k in found.items()}, **inputs}
