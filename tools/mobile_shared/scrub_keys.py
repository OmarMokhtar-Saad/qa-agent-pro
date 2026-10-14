"""Sensitive-key vocabulary and header sets for the redaction nets."""

from __future__ import annotations

import re

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
# `_` IS A WORD CHARACTER, so `\btoken\b` has no boundary to find in
# `access_token` and matched only the BARE word. Every real-world spelling was
# therefore unmasked -- access_token, refresh_token, id_token, auth_token,
# bearer_token, session_token, csrf_token -- and the same `\b` bug left
# `client_secret` and `app_secret` open while plain `secret` was masked. Measured
# against a captured plaintext request line: a JWT in `?access_token=` reached
# the report whole, while `?password=` beside it was redacted, which is the worst
# possible shape -- the page LOOKS scrubbed.
#
# So these two are matched anywhere inside the key, not on word boundaries. The
# test is applied to a KEY, which is a bare identifier, never to prose, so a
# substring match here cannot shred an ordinary sentence.
# `sid` / `session*` / `pin` are here on RISK ASYMMETRY rather than certainty. A
# session id is a credential -- it is what session hijacking steals -- and a PIN
# is one outright. Both can also name something harmless. Over-masking costs a
# tester one `<redacted>` in a report they can re-run; under-masking puts a live
# session into a file they email to a colleague. `code` is deliberately NOT here:
# an OAuth code is a credential but `code` is far more often a country or status
# code, it is single-use and short-lived, and masking it would shred ordinary
# rows. That one is a judgement, and it is written down rather than silent.
_KEY_AUTH = (
    r"\bpassword\w*|\bemail\w*|\w*token\w*|\bauthorization\b|\bapi_?key\b"
    r"|\w*secret\w*|\bjwt\b|\bbearer\b|\botp\b|\bpasswd\w*"
    r"|\bsid\b|\bsession\w*|\bpin\b"
)
SENSITIVE_KEY_RE = re.compile(
    "|".join((_KEY_NAMES, _KEY_CONTACT, _KEY_IDENTITY, _KEY_DEMOGRAPHICS, _KEY_AUTH)),
    re.I,
)

# Short values are not identifiers, and masking them would shred ordinary payloads.
MIN_SENSITIVE_LEN = 6
