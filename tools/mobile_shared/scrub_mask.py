"""Header and bare-value masks for the redaction nets."""

from __future__ import annotations

from tools.mobile_shared.scrub_keys import (
    FIXTURE_SECRETS,
    MIN_SENSITIVE_LEN,
    SECRET_HEADERS,
)


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
