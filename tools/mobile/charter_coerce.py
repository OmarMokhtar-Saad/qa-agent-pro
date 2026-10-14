"""Coercers and the raw-input verdicts (defaults used, stop condition).

Split from :mod:`tools.mobile.charter`, which re-exports every name here.
"""

from __future__ import annotations

from tools.mobile.charter_consts import (
    MAX_BUDGET_UNITS,
    MAX_SCOPE_ITEM_CHARS,
    MAX_SCOPE_ITEMS,
)
from tools.mobile.charter_schema import ALIASES, DEFAULTS_KEY, STOP_ON, defaults


def _one_line(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


# A BOUNDED integer. Never raises, and never returns an unbounded one.
#
# The clamp is not tidiness. ``json.loads`` accepts ``1e300`` happily and
# ``int(float(1e300))`` is a 301-digit integer -- only ``>= 1e309`` reaches
# the ``OverflowError`` path -- so a charter well inside
# :data:`MAX_CHARTER_BYTES` could put two such numbers into the budget
# clause of :func:`describe_terms` and push the CHARTER clause that follows
# it past the report's 800-character clip. The clip PROPERTY survives; the
# sentence it used to name does not -- "NOT yet steering" was retired in
# the same commit that wired depth, scope and destructive -- so this names
# the clause by POSITION instead, exactly the way op 8 re-points the clip
# pin. A disclosure a caller can
# push off the page is a disclosure that was not made, so the bound is on
# the VALUE, at the one producer of it. Graded by mutant M12.
def _int(value: object, fallback: int = 0) -> int:
    """A BOUNDED integer. Never raises, and never returns an unbounded one."""
    try:
        number = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(-MAX_BUDGET_UNITS, min(MAX_BUDGET_UNITS, number))


def _choice(value: object, allowed: tuple, fallback: str) -> str:
    text = _one_line(value, 40).lower()
    text = ALIASES.get(text, text)
    return text if text in allowed else fallback


def _items(raw: object) -> list:
    """A scope list: bounded lines, blanks dropped, never raises.

    A NON-LIST is REJECTED outright rather than coerced. ``list("home")`` is
    ``['h', 'o', 'm', 'e']``, so coercing a JSON string here would turn one
    scope line into four one-character scope entries -- each of which then
    reaches the model on every turn and the tester in the report. A caller that
    sends a string sent the wrong shape, and the honest answer to the wrong
    shape is an empty scope, which the report then NAMES as a default used.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out: list = []
    for item in list(raw)[:MAX_SCOPE_ITEMS]:
        text = _one_line(item, MAX_SCOPE_ITEM_CHARS)
        if text:
            out.append(text)
    return out


def _unset(body: object, field: str) -> bool:
    """Did the RAW input leave *field* for the server to decide?

    Absent, ``None``, or empty counts as unanswered; anything else -- including
    an answer that happens to equal the default -- counts as ANSWERED. That
    distinction is the whole reason this is judged against the raw input: a
    normalised charter has every field filled, so comparing one against the
    defaults cannot tell "unset" from "set to the default value", and a tester
    who answered ``depth: both`` would be told they did not answer.
    """
    if not isinstance(body, dict) or field not in body:
        return True
    value = body.get(field)
    if value is None:
        return True
    if isinstance(value, (str, list, tuple, dict, set)) and not value:
        return True
    return False


def _sent(value: object) -> str:
    """The tester's own answer, bounded, for a refusal message.

    Not :func:`_one_line`: that renders ``0`` as ``""`` (``str(value or "")``),
    and ``0`` is a value a tester can legitimately send for a budget axis --
    rendering it as empty would report an answered field as substituted.
    """
    return " ".join(str(value).split())[:40]


def defaults_used(charter: object) -> list:
    """The fields the tester did not set, in schema order. ONE producer.

    Given a RAW charter this derives the verdict with :func:`_unset`. Given a
    NORMALISED one it returns the verdict :func:`normalize` already recorded --
    the same value, not a second derivation, because the normalised charter no
    longer carries the evidence the derivation needs.

    The report names these. ``stop_on`` can appear here though no question ever
    asked it, and that is the point: the cap on questions is paid for by naming
    what was assumed.
    """
    body = charter if isinstance(charter, dict) else {}
    blank = defaults()
    recorded = body.get(DEFAULTS_KEY)
    if isinstance(recorded, (list, tuple)):
        return [str(field) for field in recorded if str(field) in blank]
    return [field for field in blank if field != "goal" and _unset(body, field)]


def stop_on(charter: object) -> str:
    """The charter's stop condition, defaulted. One reader's worth of coercion."""
    body = charter if isinstance(charter, dict) else {}
    return _choice(body.get("stop_on"), STOP_ON, "budget")
