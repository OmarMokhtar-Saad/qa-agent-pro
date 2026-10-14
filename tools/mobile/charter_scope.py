"""The scope lines, the scope sentence and the three-valued scope verdict."""

from __future__ import annotations

from tools.mobile.charter_coerce import _items
from tools.mobile.charter_consts import (
    MAX_SCOPE_ITEM_CHARS,
    MAX_SCOPE_MATCH_CHARS,
    OFF_CHARTER_NOTE,
    SCOPE_ELEMENT_FIELDS,
    SCOPE_IN,
    SCOPE_OUT,
    SCOPE_SCREEN_FIELDS,
    SCOPE_UNCONFIRMED,
    UNCONFIRMED_NOTE,
)


def scope_lines(charter: object) -> dict:
    """``{"include": [...], "exclude": [...]}``, coerced. ONE producer.

    Read by the packet builder and by :func:`scope_verdict`, so the lines the
    model is TOLD about and the lines the server JUDGES against are the same
    list. Two derivations would drift, and the drift is invisible from either
    end: the model would be told one scope while the report recorded another.
    """
    body = charter if isinstance(charter, dict) else {}
    scope = body.get("scope")
    scope = scope if isinstance(scope, dict) else {}
    return {
        "include": _items(scope.get("include")),
        "exclude": _items(scope.get("exclude")),
    }


def _scope_phrase(scope: object) -> str:
    body = scope if isinstance(scope, dict) else {}
    include = _items(body.get("include"))
    exclude = _items(body.get("exclude"))
    if not include and not exclude:
        return "the whole app"
    said = []
    if include:
        said.append("inside " + ", ".join(include))
    if exclude:
        said.append("staying out of " + ", ".join(exclude))
    return "; ".join(said)


def scope_sentence(charter: object) -> str:
    """The scope as one plain phrase, for the PACKET.

    Shares :func:`_scope_phrase` with the report sentence, so the tester and
    the tester's model read the same scope in the same words.
    """
    body = charter if isinstance(charter, dict) else {}
    return _scope_phrase(body.get("scope"))


def _named(body: object, fields: tuple) -> list:
    """The ALLOWLISTED string fields of one record. ONE helper, both levels.

    Screen and element are read through the same function so the two lists
    cannot acquire different rules: :data:`SCOPE_SCREEN_FIELDS` and
    :data:`SCOPE_ELEMENT_FIELDS` are the whole difference between them.
    """
    record = body if isinstance(body, dict) else {}
    out = []
    for key in fields:
        value = record.get(key)
        if isinstance(value, str) and value:
            out.append(value)
    return out


def _screen_terms(screen: object) -> str:
    """WHAT THE TESTER CAN SEE on this screen, lower-cased and bounded.

    An ALLOWLIST, not every string the record carries -- see
    :data:`SCOPE_SCREEN_FIELDS` for the measured failure that decided it and
    for the honest statement of what the key-agnostic form bought and cost.
    In one line: ``screen_id``, ``hash``, ``package``, ``dialog_package``,
    ``activity``, ``rid`` and ``cls`` are IDENTIFIERS the tester never wrote,
    and a scope line matching one of those identifies nothing.

    The residual risk is stated rather than hidden: a provider that renames
    ``text``/``desc`` empties the haystack, and the verdict then fails
    ``in_scope``/``not_confirmed`` -- the direction that explores a screen it
    might have avoided, never the direction that abandons the whole app.
    """
    body = screen if isinstance(screen, dict) else {}
    parts = _named(body, SCOPE_SCREEN_FIELDS)
    for element in list(body.get("elements") or []):
        parts.extend(_named(element, SCOPE_ELEMENT_FIELDS))
    return " ".join(parts).lower()[:MAX_SCOPE_MATCH_CHARS]


def _needle(line: object) -> str:
    return " ".join(str(line or "").split()).lower()[:MAX_SCOPE_ITEM_CHARS]


# Is THIS screen inside the charter's scope? ONE producer, THREE answers.
#
# ``{"state", "matched", "note"}``, where ``state`` is :data:`SCOPE_IN`,
# :data:`SCOPE_OUT` or :data:`SCOPE_UNCONFIRMED`.
#
# **Every answer identifies POSITIVELY.** ``out_of_scope`` means an EXCLUDE
# line was FOUND in what the tester can SEE on the screen (the allowlisted
# fields of :func:`_screen_terms`, never an identifier) -- never "no
# include matched", which is a different fact and has its own name. That third answer is what keeps the
# rule honest: a tester writes "Checkout" and the screen says "Basket", and
# a two-valued predicate would either explore a flow it was told to avoid or
# declare the whole app out of bounds. The undeterminable row is the one
# that decides the design, so it gets its own value and its own sentence.
#
# This server does NOT stop a run on ``out_of_scope``: the visit is RECORDED
# (``explore_runner.next_turn`` appends it to ``state["off_charter"]``) and
# the model is told to leave. Nothing grades a scope-driven stop, because
# nothing implements one -- follow-up.
def scope_verdict(charter: object, screen: object) -> dict:
    """Is THIS screen inside the charter's scope? ONE producer, THREE answers."""
    lines = scope_lines(charter)
    haystack = _screen_terms(screen)
    for line in lines["exclude"]:
        needle = _needle(line)
        if needle and needle in haystack:
            return {"state": SCOPE_OUT, "matched": needle, "note": OFF_CHARTER_NOTE}
    if not lines["include"]:
        return {"state": SCOPE_IN, "matched": "", "note": ""}
    for line in lines["include"]:
        needle = _needle(line)
        if needle and needle in haystack:
            return {"state": SCOPE_IN, "matched": needle, "note": ""}
    return {"state": SCOPE_UNCONFIRMED, "matched": "", "note": UNCONFIRMED_NOTE}
