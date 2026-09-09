"""The redaction nets, and the ONE escape that applies them.

Ported from the reference report shell. Two nets ride here, and they differ in an
important way. The VALUE net masks what the run presented as a credential -- or
what the tester typed into a device field -- so it does nothing until
:func:`learn_sensitive` or :func:`arm` has run. The PAIR net (:func:`scrub_pairs`)
masks a sensitive key's value where only the text of the pair survives, and a key
names itself, so it needs no arming and runs always.

:func:`e` is the choke point: ``html.escape(scrub_text(value), quote=True)``.
Enumerating render paths does not converge -- the reference added the value net at
one call site, then another, then another, and text kept reaching the page through
the next one -- so every path ends here. Everything that formats evidence text for
the page goes through :func:`e`, and everything written to DISK goes through
:func:`scrub_text` or :func:`scrub_json` first (the lane's two secret tests grep
every file under the cache root, so redaction at render alone is not enough).

This module knows no app. Its key list is the generic vocabulary of identifying
fields; the per-app vocabulary lives in the profile.
"""

from __future__ import annotations

import html
import json
import re
from typing import Iterable

# Headers that carry an invariant worth seeing at a glance. Four, not seven: marking six of
# ten rows is the same as marking none -- the eye stops treating the highlight as a signal.
MARKED_HEADERS = {
    "authorization",
    "apikey",
    "accept-language",
    "x-credential-nid",
    "x-credential-dependent-nid",
}

# Header values that must never reach a shared report verbatim. The credential nids are here
# for a DIFFERENT reason than the tokens: a token is a secret nobody needs to read; a
# credential nid is EVIDENCE -- it is how a report proves which patient a call acted for --
# and because it had a job to do it was once left out, and a real national id shipped in
# cleartext one line below a correctly masked bearer token. The mask keeps the last four
# characters, so two calls made for different people still LOOK different.
SECRET_HEADERS = {
    "authorization",
    "apikey",
    "x-api-key",
    "cookie",
    "set-cookie",
    "x-credential-nid",
    "x-credential-dependent-nid",
}

# Known fixture values, shown verbatim because seeing them is the POINT: they are the
# evidence that auth was actually wired onto the call. Keep this list tiny.
FIXTURE_SECRETS = {"bearer test-token", "test-key", "jwt", ""}

# The person-field family, one alternation per group so a reviewer can see what is
# covered. Key-anchored: every alternative is bounded by a word edge or the key's own
# end, so ``filename`` / ``username`` / ``hostname`` / ``serviceName`` / ``conceptName``
# never match while ``full_name_english`` / ``firstName`` / ``contactNo1`` do.
_KEY_NAMES = (
    r"\bfull_?name\w*|\bfirst_?name\w*|\blast_?name\w*|\bfirst_and_last_name\w*|"
    r"\bname_(?:arabic|english)\b|\bpatient_?name\w*|\w*Name(?:Arabic|English)\b|"
    r"\bfirstAndLastName\w*"
)
_KEY_CONTACT = r"\bcontact_?no\d*\b|\bmobile(?:_?number|_?no)?\b|\bphone(?:_?number|_?no)?\b|\bmsisdn\b"
_KEY_IDENTITY = (
    r"\bnational_?id\w*|nationalidiqama|\biqama\w*|identification_?number|\bhealth_?id\b|"
    r"\bnid\b|\bpassport\w*"
)
_KEY_DEMOGRAPHICS = r"date_?of_?birth|\bdob\b|\bbirth_?date\b|\bgender\b|\bage\b"
_KEY_AUTH = (
    r"\bpassword\w*|\bemail\w*|\btoken\b|\bauthorization\b|\bapi_?key\b|\bsecret\w*"
)
SENSITIVE_KEY_RE = re.compile(
    "|".join((_KEY_NAMES, _KEY_CONTACT, _KEY_IDENTITY, _KEY_DEMOGRAPHICS, _KEY_AUTH)),
    re.I,
)

# Short values are not identifiers, and masking them would shred ordinary payloads.
MIN_SENSITIVE_LEN = 6

# Populated per render by learn_sensitive / arm, reset by forget_sensitive so one page's
# values can never leak into the next page's mask decisions.
_SENSITIVE_VALUES: set[str] = set()


def redact_header(key: object, value: object) -> object:
    """Mask a credential header unless its value is a known fixture.

    Deliberately narrow: only SECRET_HEADERS are touched and every other header renders
    exactly as captured. The mask keeps the scheme and the last 4 characters, enough to tell
    that a token WAS sent and to match it against an environment, without carrying it.
    """
    text = str(value)
    if (
        str(key).lower() not in SECRET_HEADERS
        or text.strip().lower() in FIXTURE_SECRETS
    ):
        return value
    scheme, _, rest = text.partition(" ")
    if rest:
        return scheme + " <redacted:" + str(len(rest)) + " chars ..." + rest[-4:] + ">"
    return "<redacted:" + str(len(text)) + " chars ..." + text[-4:] + ">"


def mask_value(value: object) -> str:
    """The header mask, applied to a bare value: keep the length and the last four.

    THE TAIL IS DROPPED ON A SHORT VALUE. ``s[-4:]`` of a four-character secret is the whole
    secret, so ``"password": "12"`` would have rendered ``<redacted:2 chars ...12>`` -- the
    plaintext wearing a redaction label, which is worse than no mask because it reads as
    safe. Below MIN_SENSITIVE_LEN there is nothing to distinguish and everything to give away.
    """
    text = str(value)
    if len(text) < MIN_SENSITIVE_LEN:
        return "<redacted:" + str(len(text)) + " chars>"
    return "<redacted:" + str(len(text)) + " chars ..." + text[-4:] + ">"


def learn_sensitive(data: object) -> int:
    """Collect the identifying values this render must never print, from the run itself.

    Called ONCE before rendering, not lazily during it: a body may render before any header
    does, and a net armed halfway through the page is not a net. Shape-independent on
    purpose: it walks whatever it is given looking for ``requestHeaders``.
    """
    stack = [data]
    seen = 0
    while stack and seen < 200000:
        node = stack.pop()
        seen += 1
        if isinstance(node, dict):
            headers = node.get("requestHeaders")
            if isinstance(headers, dict):
                for key, value in headers.items():
                    if (
                        str(key).lower() in MARKED_HEADERS
                        and str(key).lower() != "accept-language"
                    ):
                        text = str(value).strip()
                        if (
                            len(text) >= MIN_SENSITIVE_LEN
                            and text.lower() not in FIXTURE_SECRETS
                        ):
                            _SENSITIVE_VALUES.add(text)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return len(_SENSITIVE_VALUES)


def arm(values: Iterable[object]) -> int:
    """Add the tester's typed values to the net before a capture is written.

    The lane types credentials onto the device from ``Context.tester_inputs``, which is never
    persisted -- so the moment a logcat slice is taken is the ONLY moment the typed value is
    known and can be masked. A value shorter than MIN_SENSITIVE_LEN is not armed, for the
    reason :func:`mask_value` gives. Returns the size of the armed set.
    """
    for value in values or ():
        text = str(value or "").strip()
        if len(text) >= MIN_SENSITIVE_LEN:
            _SENSITIVE_VALUES.add(text)
    return len(_SENSITIVE_VALUES)


def forget_sensitive() -> None:
    """Drop what the last render or capture learned."""
    _SENSITIVE_VALUES.clear()


def armed() -> int:
    """How many values the net currently holds (for tests and disclosure)."""
    return len(_SENSITIVE_VALUES)


# A key/value pair as it survives INSIDE free text, quoted or entity-quoted, with a string or
# a bare number on the right. Bounded on purpose: the key is a plain identifier of at most 41
# characters and the value stops at its closing quote or the end of a number, so this cannot
# run away over a multi-megabyte body. The string branch understands BACKSLASH ESCAPES,
# because a serialised-then-escaped JSON value can legitimately contain ``\"``.
TEXT_PAIR_RE = re.compile(
    r'(?P<q1>&quot;|")(?P<k>[A-Za-z_][A-Za-z0-9_]{0,40})(?P=q1)\s*:\s*'
    r'(?:(?P<q2>&quot;|")(?P<sv>(?:\\.|(?!&quot;)[^"\\]){0,4000})(?P=q2)'
    r"|(?P<nv>-?\d[\d.]{0,30}))"
)


def scrub_pairs(text: str) -> str:
    """Mask a sensitive key's value where only the TEXT of the pair survives.

    UNLIKE the value net, this does NOT depend on arming: a key names itself, so there is
    nothing to arm. The ``:`` test comes first -- an ordinary label has no colon and leaves.
    """
    if ":" not in text:
        return text

    def one(match: re.Match) -> str:
        if not SENSITIVE_KEY_RE.search(match.group("k")):
            return match.group(0)
        group = "sv" if match.group("sv") is not None else "nv"
        value = match.group(group)
        if not value:
            return match.group(0)
        # SPLICED BY POSITION, never by str.replace over the matched span. A replace would
        # rewrite the value wherever it appears in that span -- including INSIDE THE KEY,
        # which is reachable: `"password2": 2` matches, and replacing "2" would garble the
        # key as well as mask the value. Offsets are relative to the match start.
        low = match.start(group) - match.start(0)
        high = match.end(group) - match.start(0)
        whole = match.group(0)
        return whole[:low] + mask_value(value) + whole[high:]

    return TEXT_PAIR_RE.sub(one, text)


def scrub_text(text: object) -> str:
    """Mask every armed value wherever it appears in free text, every sensitive
    key/value pair that survives as text, and the labelled prose values. NOT a no-op on
    an unarmed render: the pair and prose passes always run."""
    out = scrub_prose(scrub_pairs("" if text is None else str(text)))
    if not _SENSITIVE_VALUES:
        return out
    # Longest first, so a value that contains another is masked whole rather than being
    # half-rewritten by its own substring.
    for value in sorted(_SENSITIVE_VALUES, key=len, reverse=True):
        if value in out:
            out = out.replace(value, mask_value(value))
    return out


# ── free prose ──────────────────────────────────────────────────────────────────
#
# The key nets need a KEY. An SDK's prompt body carries the patient as prose --
# ``FACTS: patient: age 42, m, Riyadh, <name>, height 180cm, blood type B+`` and a
# dependents roster -- and reached disk and the page in cleartext on the first live
# capture. These are LABELLED-VALUE patterns, each bounded (no unbounded quantifier
# over the whole text), so they cannot run away over a multi-megabyte body.
_PROSE_AGE_RE = re.compile(
    r"(?i)(?P<label>\b(?:age|\u0639\u0645\u0631)\s*[:=]?\s*)(?P<value>\d{1,3})\b"
)
_PROSE_BLOOD_RE = re.compile(
    r"(?i)(?P<label>\bblood\s*type\s*[:=]?\s*)(?P<value>(?:AB|A|B|O)[+-])"
)
_PROSE_PHONE_RE = re.compile(r"(?<!\d)(?P<value>(?:\+?966|0)5\d{8})(?!\d)")
# The roster label, then up to six comma-separated tokens of at most 60 chars each.
_PROSE_PERSON_RE = re.compile(
    r"(?i)(?P<label>\b(?:patient|dependents?|\u0627\u0644\u0645\u0631\u064a\u0636|\u0627\u0644\u062a\u0627\u0628\u0639(?:\u064a\u0646)?)\s*:\s*)"
    r"(?P<list>[^,\n\u00b7]{1,60}(?:,\s*[^,\n\u00b7]{1,60}){0,5})"
)
# A token that is a bare number, a number with a unit, or a one-letter code is kept:
# ``180cm``, ``71kg``, ``m``, ``f`` say nothing about WHO.
_PROSE_KEEP_RE = re.compile(
    r"^(?:[a-z]|\d+(?:\.\d+)?\s*[a-z%]{0,6}|\w+\s+\d+(?:\.\d+)?\s*[a-z%]{0,6})$", re.I
)
# A token that carries a measurement label, or that an earlier pass already masked,
# is kept whole: ``age <redacted...>`` must not be masked a second time with its label.
_PROSE_LABELLED_RE = re.compile(
    r"^(?:age|blood\s*type|height|weight|bmi|bp|glucose|hr|spo2|temp|waistline|doses)\b|^<redacted:",
    re.I,
)


def _mask_list(match: re.Match) -> str:
    tokens = match.group("list").split(",")
    out = []
    for token in tokens:
        raw = token.strip()
        if not raw or _PROSE_KEEP_RE.match(raw) or _PROSE_LABELLED_RE.search(raw):
            out.append(token)
        else:
            lead = token[: len(token) - len(token.lstrip())]
            out.append(lead + mask_value(raw))
    return match.group("label") + ",".join(out)


def scrub_prose(text: str) -> str:
    """Mask the labelled personal values a prompt carries as prose.

    Age, blood type and a phone-shaped run are masked wherever they appear; the tokens
    after a ``patient:`` / ``dependents:`` label are masked unless they are a number, a
    measurement or a one-letter code. The LABEL always survives, so a reader sees that a
    value stood there. Anything not labelled is NOT touched -- stated in the docs.
    """
    if not text:
        return text
    out = _PROSE_AGE_RE.sub(
        lambda m: m.group("label") + mask_value(m.group("value")), text
    )
    out = _PROSE_BLOOD_RE.sub(
        lambda m: m.group("label") + mask_value(m.group("value")), out
    )
    out = _PROSE_PHONE_RE.sub(lambda m: mask_value(m.group("value")), out)
    return _PROSE_PERSON_RE.sub(_mask_list, out)


def scrub_json(obj: object, _key: object = None) -> object:
    """Walk a parsed body, masking by key name and by value.

    THE KEY IS CHECKED FIRST, BEFORE ANY RECURSION, and this order is the whole point.
    Recursing first would re-key each child by its OWN field name and throw the parent
    away, so ``{"patient_name": {"first": "John", "last": "Doe"}}`` would sail through
    untouched. A matched key masks its WHOLE value, scalar or subtree, and a list under a
    sensitive key is still that key's value.
    """
    if _key and SENSITIVE_KEY_RE.search(str(_key)):
        if isinstance(obj, (dict, list)):
            # Masked as its serialised self, so two different subtrees still differ.
            return mask_value(json.dumps(obj, ensure_ascii=False, sort_keys=True))
        text = str(obj)
        return obj if not text or text in ("None", "null") else mask_value(text)
    if isinstance(obj, dict):
        return {key: scrub_json(value, key) for key, value in obj.items()}
    if isinstance(obj, list):
        return [scrub_json(value, _key) for value in obj]
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        # An id arriving as a NUMBER under a key nobody listed is still an id, but only the
        # value net can see it -- and only when this run presented it as a credential.
        return mask_value(obj) if str(obj) in _SENSITIVE_VALUES else obj
    return obj


def e(value: object, quote: bool = True) -> str:
    """Escape for HTML -- and mask on the way through. THIS IS THE CHOKE POINT."""
    return html.escape(
        scrub_text(value if isinstance(value, str) else str(value)), quote
    )
