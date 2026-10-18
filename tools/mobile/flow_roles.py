"""What a field is for (otp, password, id), whatever the app calls it."""

from __future__ import annotations

import re

_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


def tokens(name: str) -> frozenset:
    """The lowercase words of *name* (split on non-alphanumerics and camelCase)."""
    return frozenset(w.lower() for w in _WORDS.findall(str(name)))


#: Words that say what a secret is for, whatever the app calls the field.
ROLES = (
    ("otp", frozenset({"otp", "otc", "sms", "2fa", "mfa"})),
    ("password", frozenset({"password", "pass", "pwd", "passcode", "pin"})),
    ("id", frozenset({"id", "iqama", "national", "nid", "username", "user"})),
)


#: A bare "code" is a one-time code only beside one of these ("Promo code" is not).
_CODE_CONTEXT = frozenset(
    {
        "verification",
        "verify",
        "security",
        "confirmation",
        "auth",
        "authentication",
        "one",
        "time",
        "login",
        "activation",
    }
)


def role(name: str) -> str:
    """The role of *name* (``otp``, ``password``, ``id``), or ``""`` when it has none."""
    words = tokens(name)
    if "code" in words and words & _CODE_CONTEXT:
        return "otp"
    return next((r for r, known in ROLES if words & known), "")


def role_problem(index: int, step: dict, name: str) -> str:
    """A `{param}` fill must match its field: never a secret role, never another role."""
    field = role(str(step.get("label") or ""))
    bound = role(name)
    if not (field in ("otp", "password") or (field and bound and field != bound)):
        return ""
    return (
        "step %d: `%s` is bound to a %s field; use a secret step with a `field`, "
        "or a parameter that names what the field takes" % (index, name, field)
    )
