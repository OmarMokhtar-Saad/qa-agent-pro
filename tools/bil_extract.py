"""EN/AR language-pair extraction from ticket text."""

from __future__ import annotations

import logging
import re

from tools import bil_consts, bil_key
from tools.bil_cells import _cells, _pair_from_cells
from tools.bil_key import _KEY_RE
from tools.bil_pair import LanguagePair, PairSpec
from tools.bil_script import _AR_CHAR_RE, _AR_RUN_RE

logger = logging.getLogger("tools.bilingual")

# --- Language-pair extraction -------------------------------------------------


_EN_LABEL_RE = re.compile(r"\b(?:EN|ENG|ENGLISH)\b\s*[:=]", re.IGNORECASE)
_AR_LABEL_RE = re.compile(
    "(?:\\b(?:AR|ARA|ARABIC)\\b|العربية)\\s*[:=]",
    re.IGNORECASE,
)


def _raw_pair(line: str) -> tuple[str, str] | None:
    """(en_raw, ar_raw) of a free-text line, or None when it has no Arabic run."""
    en_m = _EN_LABEL_RE.search(line)
    ar_m = _AR_LABEL_RE.search(line)
    if en_m and ar_m:
        if en_m.start() < ar_m.start():
            return line[en_m.end() : ar_m.start()], line[ar_m.end() :]
        return line[en_m.end() :], line[ar_m.end() : en_m.start()]
    run = _AR_RUN_RE.search(line)
    if not run:
        return None
    return line[: run.start()] or line[run.end() :], run.group(0)


def _pair_from_line(line: str) -> tuple[str, str, str] | None:
    """(key, en, ar) from a free-text line, or None."""
    if not _AR_CHAR_RE.search(line):
        return None
    key = ""
    m = _KEY_RE.search(line)
    if m:
        key = bil_key.normalize_key(m.group(1))
    raw = _raw_pair(line)
    if raw is None:
        return None
    en, ar = bil_key._clean_value(raw[0]), bil_key._clean_value(raw[1])
    if not en or not ar:
        return None
    return key, en, ar


def _parsed_lines(text: str):
    """Yield (key, en, ar, line) for every line that parses as a pair."""
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if len(line) < 3 or not _AR_CHAR_RE.search(line):
            continue
        parsed = _pair_from_cells(_cells(line)) or _pair_from_line(line)
        if not parsed:
            continue
        key, en, ar = parsed
        if not en or not ar:
            continue
        yield key, en, ar, line


def _keyed(items):
    """Give every key-less parsed pair a synthetic ``PAIR01``-style key."""
    auto = 0
    for key, en, ar, line in items:
        if not key:
            auto += 1
            key = f"PAIR{auto:02d}"
        yield key, en, ar, line


# Parse documented EN/AR message pairs out of ticket text.
#
# Recognises markdown/tab tables (``| DM01 | Login failed | ... |``), labelled
# lines (``DM01 - EN: "Login failed" / AR: "..."``) and bare
# ``English text - Arabic text`` lines. A pair with no ``DM##``-style key gets
# a synthetic ``PAIR01`` key so it is still enforceable.
#
# Returns [] for text with no Arabic. Never raises -- a parse failure returns
# whatever was parsed so far.
def extract_language_pairs(
    text: str, limit: int = bil_consts.MAX_PAIRS
) -> list[LanguagePair]:
    """Parse documented EN/AR message pairs out of ticket text."""
    pairs: list[LanguagePair] = []
    try:
        seen: set[str] = set()
        cap = max(1, int(limit or bil_consts.MAX_PAIRS))
        for key, en, ar, line in _keyed(_parsed_lines(text)):
            if key in seen:
                continue
            seen.add(key)
            pairs.append(LanguagePair(PairSpec(key, en, ar, line[:300])))
            if len(pairs) >= cap:
                logger.info("extract_language_pairs: hit the %d-pair cap", cap)
                break
    except Exception:
        logger.exception("extract_language_pairs failed - returning partial result")
    return pairs
