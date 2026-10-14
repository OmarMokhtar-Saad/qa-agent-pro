"""The pair and query nets: a sensitive key's value masked inside free text."""

from __future__ import annotations

import re

from tools.mobile_shared.scrub_keys import SENSITIVE_KEY_RE
from tools.mobile_shared.scrub_mask import mask_value

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


def _mask_pair(match: re.Match) -> str:
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


def scrub_pairs(text: str) -> str:
    """Mask a sensitive key's value where only the TEXT of the pair survives.

    UNLIKE the value net, this does NOT depend on arming: a key names itself, so there is
    nothing to arm. The ``:`` test comes first -- an ordinary label has no colon and leaves.
    """
    if ":" not in text:
        return text
    return TEXT_PAIR_RE.sub(_mask_pair, text)


# A key/value pair as it survives in a URL QUERY STRING or a form body: ``?token=abc`` /
# ``&api_key=abc``. The pair net above reads JSON-shaped text and cannot see this shape,
# which was found the moment a plaintext request line was captured off the wire: an app
# that puts a token in a URL puts it exactly here. Bounded the same way -- the key is a
# plain identifier of at most 41 characters and the value stops at the next separator.
QUERY_PAIR_RE = re.compile(
    r"(?P<lead>[?&;]|^|\s)(?P<k>[A-Za-z_][A-Za-z0-9_.\-]{0,40})=(?P<v>[^&;\s\"'<>#]{1,4000})"
)


def _mask_query_pair(match: re.Match) -> str:
    if not SENSITIVE_KEY_RE.search(match.group("k")):
        return match.group(0)
    return match.group("lead") + match.group("k") + "=" + mask_value(match.group("v"))


def scrub_query(text: str) -> str:
    """Mask a sensitive key's value where the pair survives as ``key=value``.

    Like :func:`scrub_pairs`, this needs no arming: the key names itself. The ``=`` test
    comes first so ordinary prose leaves without paying for the regex.
    """
    if "=" not in text:
        return text
    return QUERY_PAIR_RE.sub(_mask_query_pair, text)
