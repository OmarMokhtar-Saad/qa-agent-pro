"""Refusals: the fields whose answer a coercer replaced, and the message."""

from __future__ import annotations

from tools.mobile.charter_coerce import _choice, _int, _sent, _unset
from tools.mobile.charter_consts import MAX_BUDGET_UNITS
from tools.mobile.charter_schema import (
    ALIASES,
    DEPTHS,
    DESTRUCTIVE,
    REJECTABLE,
    REJECTED_KEY,
    STOP_ON,
    defaults,
)


# The fields whose ANSWER was REPLACED, what was sent, and what was used.
#
# **This exists because a coercer is a REFUSAL.** :func:`_choice` replaces an
# out-of-enum answer with the field's default, and :func:`_unset` counts the
# field as ANSWERED -- the raw value is a non-empty string -- so it appears
# in NO defaults clause and in no clamp clause, and :func:`describe_terms`
# then renders the SUBSTITUTED value in the present indicative. Measured on
# the shipped code: ``stop_on: "first-finding"`` became ``budget`` and the
# report said "stopping on budget"; ``depth: "negatives"`` became ``both``;
# ``destructive: "yes"`` became ``none``. ``stop_on`` is WIRED, so that is a
# change of BEHAVIOUR and not only of wording -- a tester who asked to stop
# on the first finding got a run that does not.
#
# That violates the invariant this change itself adopted: **every site that
# refuses or reduces what the model asked for must refuse BY NAME in the
# same reply.** The asymmetry was the tell -- :func:`parse` refuses oversize
# and unparseable input by name, the budget clamp names the axis that
# clamped, the extension refuses by name, and the enum fallback alone stayed
# silent. The verdict is derived HERE, from the RAW body, once, because the
# normalised charter carries only the substituted value and cannot support
# the derivation afterwards.
#
# One mutant per coerced field (M13a ``depth``, M13b ``destructive``,
# M13c ``stop_on``, M13d ``budget``): a single-field fixture would grade the
# fixture and not the rule.
def _rejected(body: object) -> list:
    """The fields whose ANSWER was REPLACED, what was sent, and what was used."""
    src = body if isinstance(body, dict) else {}
    return _rejected_enums(src) + _rejected_budget(src)


_ENUM_FIELDS = (
    ("depth", DEPTHS),
    ("destructive", DESTRUCTIVE),
    ("stop_on", STOP_ON),
)


def _enum_row(src: dict, field: str, allowed: tuple) -> dict | None:
    """One closed-enum field of *src* whose answer was replaced, else None."""
    if _unset(src, field):
        return None
    used = _choice(src.get(field), allowed, defaults()[field])
    sent_norm = _sent(src.get(field)).lower()
    sent_norm = ALIASES.get(sent_norm, sent_norm)
    if used == sent_norm:
        return None
    return {
        "field": field,
        "sent": _sent(src.get(field)),
        "used": used,
        "choices": list(allowed),
    }


def _rejected_enums(src: dict) -> list:
    """The coerced closed-enum fields (depth, destructive, stop_on) of *src*."""
    rows = (_enum_row(src, field, allowed) for field, allowed in _ENUM_FIELDS)
    return [row for row in rows if row is not None]


def _rejected_budget(src: dict) -> list:
    """The budget axes of *src* whose number was replaced."""
    out: list = []
    budget = src.get("budget")
    budget = budget if isinstance(budget, dict) else {}
    for axis in ("steps", "minutes"):
        if _unset(budget, axis):
            continue
        used_number = max(0, _int(budget.get(axis), 0))
        if _sent(budget.get(axis)) != str(used_number):
            out.append(
                {
                    "field": "budget." + axis,
                    "sent": _sent(budget.get(axis)),
                    "used": str(used_number),
                    "choices": ["a whole number from 0 to " + str(MAX_BUDGET_UNITS)],
                }
            )
    return out


def _refusal_message(rejected: list) -> str:
    """The refusal for one or more fields :func:`_rejected` flagged.

    Plain ``str()`` on the sent value, NEVER ``repr()``: this message is what
    the tester reads back verbatim, and ``repr()`` would wrap the value in
    quotes and break the exact substring an existing test asserts on --
    ``"depth was sent as xyz"``, not ``"depth was sent as 'xyz'"``.
    """
    sentences: list = []
    for item in rejected:
        sentences.append(
            str(item.get("field"))
            + " was sent as "
            + str(item.get("sent"))
            + ", which is not one of: "
            + ", ".join(str(choice) for choice in item.get("choices") or [])
            + "."
        )
    return " ".join(sentences)


def _recorded_rows(recorded: list | tuple) -> list:
    """The recorded refusals, each field, value and choice re-bounded."""
    out: list = []
    for item in recorded:
        if not isinstance(item, dict) or str(item.get("field")) not in REJECTABLE:
            continue
        out.append(
            {
                "field": str(item.get("field")),
                "sent": _sent(item.get("sent")),
                "used": _sent(item.get("used")),
                "choices": [
                    _sent(choice) for choice in list(item.get("choices") or [])[:8]
                ],
            }
        )
    return out


# The refusals for this charter. ONE producer, :func:`defaults_used`'s shape.
#
# Given a RAW charter this derives them; given a NORMALISED one it returns
# what :func:`normalize` recorded, because the normalised charter carries
# only the substituted values. Host spoofing is closed exactly as it is for
# the defaults verdict: :func:`normalize` strips every non-schema key --
# :data:`REJECTED_KEY` included -- before it derives anything, and every
# field, value and choice returned here is re-bounded on the way out so a
# stored record cannot grow the sentence.
def rejected_fields(charter: object) -> list:
    """The refusals for this charter. ONE producer, :func:`defaults_used`'s shape."""
    body = charter if isinstance(charter, dict) else {}
    recorded = body.get(REJECTED_KEY)
    if not isinstance(recorded, (list, tuple)):
        return _rejected(body)
    return _recorded_rows(recorded)
