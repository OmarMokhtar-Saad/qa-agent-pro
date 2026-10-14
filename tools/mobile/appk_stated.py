"""Stated-credential patterns: a secret word assigned or followed by a digit run."""

from __future__ import annotations

import re

#: A secret word ASSIGNED a value: ``password=ab12`` (any 3+ chars after
#: ``=``), or ``password is Hunter`` / ``pin: Tr0ub`` (after ``is``/``was``/
#: ``:``, a token that is not a plain lowercase word). Narrower than
#: CREDENTIAL_TERMS: "login is required" and "the code is sent" stay prose.
#: ``pass`` counts only in the ``=`` form: "Pass: Yes" is a test verdict.
#: KNOWN LIMIT: an all-lowercase word after ``is`` or ``:`` ("password:
#: hunter") reads as prose, and so does an all-lowercase hyphenated
#: passphrase ("passphrase is correct-horse-battery"); the held-value check
#: is the net for both.
_STATED_WORDS = r"password|passwd|passcode|passphrase|pin|otp|secret|token|apikey|cvv"
_ASSIGNED_RE = re.compile(
    r"\b(?i:pass|%s)\s*=\s*[^\s,;]{3,}" % _STATED_WORDS
    + r"|\b(?i:%s)(?:\s+(?i:is|was)\s+|\s*:\s*)" % _STATED_WORDS
    + r"(?![a-z]+(?:-[a-z]+)*(?:[\s,;.!?)]|$))[^\s,;]{3,}"
)
#: Four or more digits right after a secret word, split by spaces or dashes
#: (spaces around the dash allowed): "pin 1 - 2 - 3 - 4", "otp: 48 21",
#: "token 1-2-3-4". After a word that names a code (``_CODE_WORDS``) dots,
#: commas and slashes split a run too, so "pin: 1,2,3,4", "pin 1/2/3/4" and
#: "password 1.2.3.4" are refused while "token 2.0.1.3 build", "secret
#: 10.0.2.2" and "token 1, 2, 3, 4 appear" read as a version, a host or a list.
#: Only after the word, so "version 1.2.3.4" and "wait 2-3 s" stay prose. Any
#: number of is/was/at/to/on may sit between ("pin at 123 456", "pin is at to
#: 1234"), and so may a line break ("pin\nis 1234": the run is matched over
#: the whole note, not per line). The prefix has one way to spend each space,
#: and each connector needs a word, so a long run of spaces or connectors costs
#: linear time. After "pin" only, ``_states_digits`` lets a measurement or a
#: date through. The loosening is deliberate: "token 1,2,3,4" and "secret
#: 1/2/3/4" read as prose, since a token or secret is rarely a digit run.
_CODE_WORDS = r"pin|otp|passcode|cvv|password|passwd|passphrase"
_SPACE_SEP = r"(?:\s*-\s*|\s+)"
_CODE_SEP = r"(?:\s*[-,/.]\s*|\s+)"
_WORD_PREFIX = r"(?:\s+(?i:is|was|at|to|on)\b)*(?:\s*[:=])?\s*"
_STATED_DIGITS_RE = re.compile(
    r"\b(?:((?i:%s))%s(\d(?:%s?\d){3,})|((?i:%s))%s(\d(?:%s?\d){3,}))(?!\d)"
    % (_CODE_WORDS, _WORD_PREFIX, _CODE_SEP, _STATED_WORDS, _WORD_PREFIX, _SPACE_SEP)
)
#: Only "pin" is ever measured or dated (a drag pin, a pinned view or event),
#: so "otp 123 456 pt" and "cvv 12/25" are codes. A measurement is a run whose
#: every group has 3 or more digits, a thousands comma joining its group ("pin
#: 1,000 px") + a layout unit (px, dp, sp, pt; a time unit or "x" does not
#: count, since "pin 123 456 s" is a code) + no more digits, even behind
#: punctuation or "is" ("pin 100 200 px 4321", "pin 100 200 px: 4321" are
#: PINs). A run of 1- or 2-digit groups or a mix ("pin 12 34 px", "pin 123 4
#: px") is still a PIN, and so is a run of 6 or more digits in all ("pin 123
#: 456 px", "pin 100 200 px"): split or not, that is a 6-digit code, the same
#: as ``_DIGITS_RE``. Drag coordinates read as prose without the word "pin".
_PROSE_WORD = "pin"
_UNIT_AFTER_RE = re.compile(r"\s*(?i:px|dp|sp|pt)\b")
_MORE_DIGITS_RE = re.compile(r"[^\w\n]*(?:(?i:is|was|and|then|at|or|to)\b[^\w\n]*)*\d")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_MEASURE_GROUP_DIGITS = 3
_MEASURE_MAX_DIGITS = 6
#: A date is two 1- or 2-digit groups split by one slash, month/day or
#: day/month: "pin 12/31 on the map". No year: a 4-digit year is refused as a
#: value anyway (``_is_value_token``). ASCII digits only: ``int`` would read
#: fullwidth digits too.
_DATE_RE = re.compile(r"([0-9]{1,2})/([0-9]{1,2})")
#: Every line boundary ``str.splitlines`` knows, so a bare CR, U+2028, NEL,
#: VT or FF after "pin" starts a new line just as LF and CRLF do.
_LINE_BREAK_RE = re.compile(r"[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def _states_digits(segment: str) -> bool:
    """A ``_STATED_DIGITS_RE`` run that is not a pin measurement or date.

    A run on a later line than "pin" is its value, as in a label above a field:
    "pin\\n1234 px" is a PIN.
    """
    for match in _STATED_DIGITS_RE.finditer(segment):
        word, run = (g for g in match.groups() if g)
        if word.lower() != _PROSE_WORD or _LINE_BREAK_RE.search(
            match.group()[: -len(run)]
        ):
            return True
        if not (_is_measurement(segment, match.end(), run) or _is_date(run)):
            return True
    return False


def _is_measurement(segment: str, end: int, run: str) -> bool:
    unit = _UNIT_AFTER_RE.match(segment, end)
    if not unit or _MORE_DIGITS_RE.match(segment, unit.end()):
        return False
    if not run.isascii():
        return False
    groups = re.findall(r"\d+", _THOUSANDS_RE.sub("", run))
    if sum(len(g) for g in groups) >= _MEASURE_MAX_DIGITS:
        return False
    return min(len(g) for g in groups) >= _MEASURE_GROUP_DIGITS


def _is_date(run: str) -> bool:
    date = _DATE_RE.fullmatch(run)
    if not date:
        return False
    first, second = int(date.group(1)), int(date.group(2))
    return (1 <= first <= 12 and 1 <= second <= 31) or (
        1 <= second <= 12 and 1 <= first <= 31
    )
