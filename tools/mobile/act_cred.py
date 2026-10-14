"""Credential detection: the mask, the term list and the by-name reading."""

from __future__ import annotations

from pydantic import BaseModel

from config.settings_guard import never_raise
from tools.mobile import perception
from tools.mobile.action_schema import VALUE_BEARING_OPS

SECRET_MASK = "***"

#: Words that make a field a CREDENTIAL by name, whatever the model marked.
#:
#: The list is enumerated rather than described because a vague rule is not an
#: implementable one. It is checked as WHOLE WORDS -- camel-case split first --
#: against the action's ``field`` and its target's ``rid``, ``text`` and ``id``,
#: so ``com.x:id/pass`` matches ``pass`` and ``Compass`` matches nothing.
#:
#: Deliberately broad in the SAFE direction: a promo ``code`` field is masked in
#: the report, which costs a tester one detail, and a leaked one-time code costs
#: more. It exists because a model that forgets ``secret: true`` must not be
#: able to put a credential into a checkpoint, an audit line or a page.
CREDENTIAL_TERMS: frozenset = frozenset(
    {
        "password",
        "passwd",
        "pass",
        "passcode",
        "pin",
        "otp",
        "code",
        "token",
        "secret",
        "credential",
        "credentials",
        "cvv",
        "ssn",
        "apikey",
        "auth",
        "login",
        # Added 2026-09-04 from a review that executed the code: `passphrase`
        # is ONE token and so was never covered by `pass`.
        "passphrase",
        "phrase",
        "seed",
        "mnemonic",
        "security",
        "recovery",
    }
)


# True when this action's own names say it carries a credential.
#
# Independent of the ``secret`` marker on purpose: the marker is the model's
# claim, and this is the server's own reading of the field it typed into.
#
# The tokeniser is ``perception``'s, so this package asks the question ONE
# way -- the destructive lexicon, the role vocabulary and this mask all split
# camel case and match whole words with the same code. ``perception`` imports
# nothing from here, so the direction is safe.
#
# # ONLY AN OP THAT CARRIES A VALUE. An `assert` cannot hold a typed literal,
# # so flagging one by its target's label masked its expected text for
# # nothing -- and `until_text`, on an action the same rule flagged, stayed
# # clear anyway. Inconsistent rather than unsafe, and the scope is the fix.
def is_credential_action(action: object) -> bool:
    """True when this action's own names say it carries a credential."""
    if isinstance(action, BaseModel):
        with never_raise() as g:
            payload = action.model_dump(mode="json")
        if g.failed:
            return True  # pragma: no cover - defensive
    elif isinstance(action, dict):
        payload = action
    else:
        return False
    if str(payload.get("op") or "") not in VALUE_BEARING_OPS:
        return False
    return _surface_reads_as_credential(_value_surface(payload))


def _value_surface(payload: dict) -> list:
    """Every name an action's typed-into field can be read by."""
    target = payload.get("target")
    target = target if isinstance(target, dict) else {}
    return [
        payload.get("field"),
        payload.get("label"),
        target.get("rid"),
        target.get("text"),
        target.get("id"),
        target.get("label"),
    ]


def _unreadable_name(surface: list) -> bool:
    """True when a name is in an alphabet this server cannot read."""
    # THE WHOLE SURFACE, and this reverses a narrowing that lasted one round.
    #
    # It was narrowed to `field` alone because applying it to a target's label
    # masked the tester's own Arabic QUESTION in the report that exists to show
    # it. That is a real cost and it is still real. But the narrowing reopened
    # the leak for every credential field whose dump does not carry
    # `password="true"` -- a PIN, an OTP, a custom view -- and a reviewer
    # confirmed a password rendered in clear in report.html under an Arabic
    # label. Between hiding a question and showing a credential, the credential
    # wins: masking costs a tester one line of evidence, and it is avoidable by
    # targeting with `rid`, a short id or a `role`, which the packet asks for
    # anyway.
    #
    # The element-level control added alongside it stays -- a plain `type` into
    # a password input is still refused -- because it catches the case where
    # the NAME says nothing at all. Neither is sufficient alone.
    return bool(perception.has_unreadable_text(*surface))


def _lexicon_hit(surface: list) -> bool:
    """True when a name tokenises to a credential term."""
    # FAIL CLOSED ON A NAME THIS SERVER CANNOT READ. `words` tokenises ASCII,
    # so a wholly non-Latin field name yields NO tokens and could never match
    # this list -- by construction, for every language. A reviewer confirmed a
    # value under an Arabic field name reaching report.html in clear, on the
    # lane whose validated app is an Arabic one.
    #
    # The invariant, stated because it recurs: a lexicon of names is not a
    # security boundary. "Matched nothing in my list" and "is not in an
    # alphabet I can read" are different answers, and only the first clears a
    # value for printing.
    return bool(set(perception.words(*surface)) & CREDENTIAL_TERMS)


def _surface_reads_as_credential(surface: list) -> bool:
    """True when the names are unreadable to this server or match a credential term."""
    return _unreadable_name(surface) or _lexicon_hit(surface)
