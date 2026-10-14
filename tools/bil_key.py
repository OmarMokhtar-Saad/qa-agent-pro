"""Message-key normalisation and value cleaning for the bilingual rule pack."""

from __future__ import annotations

import re

from config.settings_guard import never_raise
from tools import bil_consts

_KEY_RE = re.compile(
    r"\b((?:DM|MSG|ERR|TXT|LBL|SCR|NOTIF)[\s_-]?\d{1,3})\b", re.IGNORECASE
)
_TRIM_CHARS = " \t\"'“”«»‘’|-–—:•*/,;"


def normalize_key(raw: str) -> str:
    """Canonicalise a message key so ``DM 1`` / ``dm-01`` / ``DM001`` all match.

    Returns "" for empty or unparseable input. Never raises.
    """
    # defensive
    with never_raise():
        s = re.sub(r"[\s_-]+", "", str(raw or "")).upper()
        m = re.match(r"^([A-Z]+)0*(\d+)$", s)
        if m:
            return f"{m.group(1)}{int(m.group(2)):02d}"
        return s
    return ""


def clean_value(raw: str) -> str:
    """Strip the key token, surrounding quotes/bullets and collapse whitespace."""
    try:
        s = (raw or "").strip()
        s = _KEY_RE.sub(" ", s)
        s = s.strip(_TRIM_CHARS).strip()
        s = re.sub(r"\s{2,}", " ", s)
        return s[: bil_consts._MAX_VALUE_CHARS]
    except (AttributeError, TypeError):  # pragma: no cover - defensive
        return ""


def _clean_value(raw: str) -> str:
    """Legacy name of :func:`clean_value` (WX removes it)."""
    return clean_value(raw)
