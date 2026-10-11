"""Text, escaping and stamp helpers for the run report: the leaf every other report_* module reads."""

from __future__ import annotations

import html
import re
import time
from contextvars import ContextVar

from tools.mobile import actions as actions_mod
from tools.untrusted import GUARD_MARKERS, NEUTRALIZED

MAX_TEXT = 200


#: The same three bands the reference report uses for a case's wall clock.
LAT_BUCKETS = (
    ("fast", "under 3s", 3000),
    ("ok", "3-8s", 8000),
    ("slow", "over 8s", None),
)


_SAFE_TOKEN = re.compile(r"[^a-z0-9_]+")


_SLUG = re.compile(r"[^A-Za-z0-9_-]+")


def _text(value: object, limit: int = MAX_TEXT) -> str:
    """Coerce, drop control characters, flatten whitespace, cap.

    Independent of ``perception._clean``: by the time this module reads a store
    file, that file is an outside input again.
    """
    raw = "" if value is None else str(value)
    kept = "".join(
        " " if character in "\t\r\n" else character
        for character in raw
        if character == " " or character.isprintable()
    )
    kept = " ".join(kept.split())
    if len(kept) > limit:
        kept = kept[:limit] + "..."
    return kept


def _neutralize_markers(text: str) -> str:
    """Blunt an untrusted-content or guard marker carried in a store value.

    ``perception`` does this for SCREEN attributes, but a case's title, reason
    and trace detail never pass through it -- they come back off disk, which
    :func:`_text`'s docstring already calls an outside input again. So a hostile
    app label reached the page with the sentinel intact, and this module's own
    selfcheck caught it on a real run while 937 tests stayed green, because no
    fixture had put the sentinel in a case field.

    The markers are IMPORTED, never restated. A second copy of that literal is
    the drift already fixed once inside ``perception``, where a hardcoded copy
    would have stopped matching a reworded guard with no test noticing.
    """
    out = text
    for marker in GUARD_MARKERS:
        if marker and marker in out:
            out = out.replace(marker, NEUTRALIZED)
    return out


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


def _mask_prose(value: object) -> object:
    """*value* with every credential value this page knows of replaced by the mask.

    Runs before `_text` caps the length, so a cap can never cut a secret in half
    and leave the half the mask would have matched. Non-strings pass through.
    """
    if not isinstance(value, str) or not value:
        return value
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
    for phrase in (_PROSE_CREDENTIAL_SET, _PROSE_CREDENTIAL):
        value = phrase.sub(
            lambda m: m.group(1) + m.group(2) + actions_mod.SECRET_MASK, value
        )
    return value


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
        item = stack.pop()
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
    kept = {value for value in found if len(value) >= _SECRET_MIN}
    return tuple(sorted(kept, key=lambda value: (-len(value), value)))


def _token(value: object) -> str:
    """A store string reduced to something safe to use as a class or attribute.

    A verdict comes from a file, and this module puts it in a CLASS name and in
    a ``data-`` ATTRIBUTE NAME. An attribute name cannot be escaped, so it is
    normalised instead: ``[a-z0-9_]`` only, capped, ``unknown`` when nothing
    survives.
    """
    reduced = _SAFE_TOKEN.sub("_", _text(value, 40).lower()).strip("_")
    return reduced[:24] or "unknown"


def _slug(value: object) -> str:
    """A case id as a DOM id / URL fragment: ``[A-Za-z0-9_-]`` only, capped."""
    reduced = _SLUG.sub("-", _text(value, 40)).strip("-")
    return reduced[:40] or "case"


def _ms(value: object) -> int | None:
    """A duration from a store field, or None when it is not a measurement.

    A ZERO is not a measurement either. The runner writes ``ms: 0`` for an action
    it did not time -- a 4000 ms wait carries it -- and a bar of width zero would
    be a measurement of zero, which is the one thing this page must not draw.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number <= 0 or number != number:  # NaN
        return None
    return int(min(number, 10**9))


def fmt_ms(ms: object) -> str:
    """``412 ms`` under a second, ``2.3s`` at a second and over, ``1.4m`` past a minute."""
    number = _ms(ms)
    if number is None:
        return "—"
    if number < 1000:
        return str(number) + " ms"
    if number < 60000:
        return "%.1fs" % (number / 1000.0)
    return "%.1fm" % (number / 60000.0)


def _lat_bucket(ms: object) -> str:
    number = _ms(ms)
    if number is None:
        return ""
    for key, _label, ceiling in LAT_BUCKETS:
        if ceiling is None or number < ceiling:
            return key
    return LAT_BUCKETS[-1][0]


def _percentile(values: list, share: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * share))))
    return int(ordered[index])


def _stamp(value: object) -> str:
    """A stored moment as ``YYYY-MM-DD HH:MM:SS``, or ``"unknown"``.

    THE WHOLE CALL IS INSIDE THE GUARD, and the reason is a measured page loss,
    not tidiness. The coercion was guarded and ``localtime`` was not, so a
    manifest carrying a non-finite ``created`` -- ``json.loads`` accepts a bare
    ``Infinity`` -- raised ``OverflowError`` out of ``_facts_strip``, through
    ``_document``, into ``render``'s outer ``except``: NO PAGE AT ALL, over one
    cell. A helper that promises "any moment" must be total for any moment, and
    ``"unknown"`` is what it already returns for a moment it cannot read, so no
    caller sees a new shape.

    ``OSError`` joins the tuple because that is what ``localtime`` raises for an
    out-of-range (rather than non-finite) value on some platforms, and
    ``OverflowError`` must stay in it for `tests/test_coercion_guards.py`, whose
    ``blind_guards`` rule rejects a handler whose names are a subset of
    ``{TypeError, ValueError}``.
    """
    try:
        moment = float(value or 0)
        if moment <= 0:
            return "unknown"
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(moment))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def _short_stamp(value: object) -> str:
    """The same moment, shorter, and total for the same reason.

    NOT reachable with a disk value today: both call sites pass ``time.time()``.
    It is hardened anyway because it is the same shape one function away, and
    the reachability that protects it is a property of its CALLERS -- the next
    one to pass a stored moment would reintroduce the defect this batch has now
    fixed twice. Being unreachable, this half has no mutant, which is stated
    rather than papered over with a kill it cannot earn.
    """
    try:
        moment = float(value or 0)
        if moment <= 0:
            return "unknown"
        return time.strftime("%d %b %Y %H:%M", time.localtime(moment))
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def _epoch(value: object) -> float | None:
    """An epoch SECONDS stamp from a store field, or ``None`` when it is not one.

    Deliberately NOT :func:`_ms`, which is a DURATION coercer: it clamps to ``10**9``, and
    every real epoch second passed ``10**9`` in 2001 -- so reading a timestamp through it
    pins every stamp on this page to the same instant in 2001, and the caller that then
    divided by 1000 moved the run to 1970. Two errors that agree, and a fixture whose
    ``updated`` is a small number (2.0, 300.0) cannot see either one. That is why
    ``test_a_real_epoch_stamp_is_not_clamped`` grades this with a REAL stamp.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number <= 0:
        return None
    return number


def last_checkpoint(cases: object) -> float | None:
    """The newest ``case["updated"]`` in epoch SECONDS, or ``None`` when none carries one."""
    stamps = []
    for case in cases if isinstance(cases, (list, tuple)) else ():
        value = _epoch(case.get("updated")) if isinstance(case, dict) else None
        if value is not None:
            stamps.append(value)
    return max(stamps) if stamps else None


#: The word the page uses for it, so no section invents its own.
LAST_CHECKPOINT_LABEL = "last checkpoint"


LAST_CHECKPOINT_SUB = "derived from the newest case checkpoint; this run recorded no end time, because nothing writes one and an abandoned run has no honest end"


def elapsed_note(manifest: object, cases: object) -> str:
    """Elapsed, or the NAME of whichever half is missing. Never a silent blank."""
    book = manifest if isinstance(manifest, dict) else {}
    created = _epoch(book.get("created"))
    end = last_checkpoint(cases)
    if created is None and end is None:
        return "no start time recorded, and no case has ever been checkpointed"
    if created is None:
        return "no start time recorded, so elapsed cannot be computed"
    if end is None:
        return "no case has been checkpointed yet, so elapsed cannot be computed"
    # Both halves are epoch SECONDS; fmt_ms wants milliseconds.
    return fmt_ms(max(0, int((end - created) * 1000.0)))
