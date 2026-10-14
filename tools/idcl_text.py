"""Text normalisation leaf of the identifier-collision detector."""

from __future__ import annotations

import re

from config.settings_guard import never_raise

_LABEL_MAX_CHARS = 80
_LABEL_MAX_TOKENS = 6
_TOKEN_FOLD_CHARS = 5
_MIN_TOKEN_CHARS = 4

_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_])(DF|UI|AF|BR)(\d{1,3})(?![A-Za-z0-9_])", re.IGNORECASE
)
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_LATIN_RE = re.compile(r"^[a-z]", re.IGNORECASE)

_GENERIC = frozenset(
    """
screen screens page pages dialog dialogs sheet sheets modal modals popup popups
window windows view views form forms flow flows section sections bottom
button buttons banner banners list lists item items field fields toast toasts
tabs card cards label labels alert alerts snackbar header footer icon menu
""".split()
)

_FUNCTION = frozenset(
    """
this that these those when while with without into onto over under after before
during from there here their they them your ours
system user users application customer cardholder tester
display displays displayed show shows shown open opens opened close closes closed
navigate navigates navigated taps tapped click clicks clicked press presses
select selects selected sees seen goes went land lands landed applies apply
block blocks blocked cover covers covered contain contains reach reaches
step steps main
rule rules business requirement requirements criterion criteria case cases
note notes refer refers above below also then each other same such only both
will must shall should where which what does done using used uses
""".split()
)


def _safe_label(text: object) -> str:
    with never_raise():
        raw = str(text or "")
        raw = "".join(ch if ch.isprintable() else " " for ch in raw)
        raw = raw.replace("`", "")
        raw = " ".join(raw.split())
        return raw.strip(" -=:|*#>").strip()[:_LABEL_MAX_CHARS]
    return ""


def _drop(token: str) -> bool:
    low = token.lower()
    return (
        len(low) < _MIN_TOKEN_CHARS
        or low in _FUNCTION
        or bool(_ID_RE.fullmatch(low.upper()))
    )


def _fold(tokens) -> frozenset:
    return frozenset(t.lower()[:_TOKEN_FOLD_CHARS] for t in tokens)


def _is_translation(a: frozenset, b: frozenset) -> bool:
    a_latin = [bool(_LATIN_RE.match(t)) for t in a]
    b_latin = [bool(_LATIN_RE.match(t)) for t in b]
    return (all(a_latin) and not any(b_latin)) or (all(b_latin) and not any(a_latin))
