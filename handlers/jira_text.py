"""Jira handler text guards: control-character check and the echoed-host helper."""

from __future__ import annotations

from urllib.parse import urlparse

from handlers import _reply


def _has_control_chars(value: str) -> bool:
    """True if *value* contains any character that could split a .env
    value across lines. Covers C0/C1 controls (0x00-0x1F, 0x7F-0x9F)
    plus the Unicode line/paragraph separators U+2028/U+2029 -- the full
    set ``str.splitlines()`` recognizes, so a value that survives this
    guard can never inject a rogue KEY= line when .env is next re-parsed
    (str.splitlines() also breaks on U+0085/U+2028/U+2029)."""
    return any(
        ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or ch in "  " for ch in str(value)
    )


# A hostile base_url can carry a newline: urlparse() STRIPS it, so
# the tail ("QA_UPDATE_REPO=attacker/repo") would be glued onto the
# hostname and rendered into the tester's chat. Nothing is written
# to .env any more, so this can no longer inject a setting -- but
# echoing it is still wrong, so a value carrying any line-splitting
# character loses its host mention entirely rather than being
# partially rendered. The tester still learns their input looked
# malformed -- just never the raw value.
def _echoed_jira_host(base_url: str) -> tuple[str, bool]:
    """``(host_to_echo, malformed)`` for the *base_url* argument."""
    if not base_url:
        return "", False
    if _has_control_chars(base_url):
        return "", True
    candidate = base_url if "://" in base_url else "https://" + base_url
    return _reply._clip_echo((urlparse(candidate).hostname or "").lower(), 80), False
