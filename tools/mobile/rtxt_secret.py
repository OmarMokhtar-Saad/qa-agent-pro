"""Report secret masking: the credential phrases, the run-scoped list and the one `esc` funnel."""

from __future__ import annotations

import html
import re
from contextvars import ContextVar

from tools.mobile import actions as actions_mod
from tools.mobile.rtxt_clean import MAX_TEXT, _neutralize_markers, _text

#: A credential NAMED in prose, and the value written after it: "password Qwerty123",
#: "OTP 4821", "national ID" and its ten digits. A goal, a case title and a turn's action are
#: written by a person, and people put the login in the sentence. The value must carry
#: a digit -- "the password field" and "Tapped the password" name no value. A
#: digitless value is caught by `_PROSE_CREDENTIAL_SET` below when the sentence marks
#: it as a value; a bare one ("password Hunter") only by the run-scoped list, when the
#: run typed it into a field marked secret or named as a credential.
#:
#: Deliberately NOT a key=value sweep: ``tenantToken=UNMARKED-CANARY-42`` still
#: renders, as `test_the_secret_refusal_cannot_catch_an_unmarked_value` pins. The terms
#: are whole words, so ``tenantToken`` is not ``token`` (which is not a term here).
_CREDENTIAL_TERMS = r"\b(national[ -]?id(?: number)?|id number|iqama|password|passwd|passcode|pin(?: code)?|otp)\b"


_PROSE_CREDENTIAL = re.compile(
    _CREDENTIAL_TERMS + r"(\s*(?:is|was|=|:)?\s*)"
    r"([^\s,;\"'<>]*\d[^\s,;\"'<>]*?)(?=[.)]?(?:[\s,;\"'<>]|$))",
    re.IGNORECASE,
)


#: The same terms with the value MARKED as one, so it needs no digit: after ``:`` or
#: ``=`` ("password: Hunter"), or quoted ("password is 'Hunter Horse'"). Group 3 keeps
#: the quotes, so the mask replaces them too.
_PROSE_CREDENTIAL_SET = re.compile(
    _CREDENTIAL_TERMS + r"(\s*[:=]\s*|\s+(?:(?:is|was)\s+)?(?=[\"']))"
    r"(\"[^\"<>\n]+\"|'[^'<>\n]+'|[^\s,;\"'<>]+?)(?=[.)]?(?:[\s,;\"'<>]|$))",
    re.IGNORECASE,
)


#: A remembered value shorter than this is masked only where the phrase above finds
#: it: "4821" blanked everywhere would eat timestamps, counts and file names.
_SECRET_MIN = 6


#: This page's remembered credential values, longest first, bound by :func:`_document`.
_SECRETS: ContextVar = ContextVar("_SECRETS", default=())


def _mask_known(value: str) -> str:
    """*value* with every remembered credential value replaced by the mask."""
    # Case-blind: the card's search index lower-cases its text before it gets here,
    # and "Hunter-Horse" typed is "hunter-horse" there.
    for secret in _SECRETS.get():
        if secret.casefold() in value.casefold():
            value = re.sub(
                r"(?<!\w)" + re.escape(secret) + r"(?!\w)",
                actions_mod.SECRET_MASK,
                value,
                flags=re.IGNORECASE,
            )
    return value


def _mask_phrases(value: str) -> str:
    """*value* with every credential value a phrase names replaced by the mask."""
    for phrase in (_PROSE_CREDENTIAL_SET, _PROSE_CREDENTIAL):
        value = phrase.sub(
            lambda m: m.group(1) + m.group(2) + actions_mod.SECRET_MASK, value
        )
    return value


def _mask_prose(value: object) -> object:
    """*value* with every credential value this page knows of replaced by the mask.

    Runs before `_text` caps the length, so a cap can never cut a secret in half
    and leave the half the mask would have matched. Non-strings pass through.
    """
    if not isinstance(value, str) or not value:
        return value
    return _mask_phrases(_mask_known(value))


def _scan_record(item: object, found: set, stack: list) -> None:
    """Take one record's credential values into *found*; queue its children on *stack*."""
    if isinstance(item, dict):
        if str(item.get("op") or "") == "type" and (
            item.get("secret") or actions_mod.is_credential_action(item)
        ):
            found.add(_text(item.get("text"), 200))
        stack.extend(item.values())
    elif isinstance(item, (list, tuple)):
        stack.extend(item)
    elif isinstance(item, str):
        for phrase in (_PROSE_CREDENTIAL, _PROSE_CREDENTIAL_SET):
            found.update(m.group(3).strip("\"'") for m in phrase.finditer(item))


def _longest_first(found: set) -> tuple:
    """The values of `_SECRET_MIN` characters or more, longest first."""
    kept = {value for value in found if len(value) >= _SECRET_MIN}
    return tuple(sorted(kept, key=lambda value: (-len(value), value)))


def _run_secrets(*records: object) -> tuple:
    """Every credential value the run's own records name or type, longest first.

    Two sources: a value written after a credential term in any string (the goal
    names the national ID the tester then types into an unmarked field), and the
    text of a ``type`` action the run marked secret or aimed at a credential field.
    Only values of `_SECRET_MIN` characters or more are kept.
    """
    found: set = set()
    stack = list(records)
    while stack:
        _scan_record(stack.pop(), found, stack)
    return _longest_first(found)


def esc(value: object, limit: int = MAX_TEXT) -> str:
    """The ONE way a store value reaches markup. ``quote=True`` is deliberate.

    Escaping alone is not enough here. It makes markup inert, which protects the
    BROWSER, but leaves a guard sentinel readable, which does not protect a
    reader -- or a model asked to summarise the report later. So markers are
    neutralised before escaping.
    """
    return html.escape(
        _neutralize_markers(_text(_mask_prose(value), limit)), quote=True
    )
