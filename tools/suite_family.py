"""Suite families: which earlier suites belong to the same source, and how much
a regenerated suite repeats the one before it. Pure, stdlib only."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import NamedTuple

from tools.jira_mcp_keys import issue_key_from_url

OVERLAP_THRESHOLD = 0.8
_KEY_RE = re.compile(r"(?<![A-Za-z0-9_-])([A-Z][A-Z0-9]+-\d+)(?![0-9A-Za-z_])")
# Standards and encodings that look like a Jira key ("UTF-8", "SHA-256", "ISO-8601").
_NOT_PROJECTS = frozenset(
    "UTF SHA ISO RFC IEEE WCAG ECMA MD AES RSA TLS SSL HTTP HTML IPV CVE CWE ANSI "
    "NFC NFD PCI GDPR UTC GMT USB TCP UDP WPA OAUTH API ARIA BASE JSON XML PDF "
    "PNG JPEG MP HEVC".split()
)
_BARE_KEY_LEN = 120
_WORD_RE = re.compile(r"[a-z]+")


def source_key(text: str | None) -> str:
    """The Jira key of *text* (URL in any form, or a short text naming ONE key), else ""."""
    raw = (text or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith(("http://", "https://")):
        return issue_key_from_url(raw.split()[0])
    if len(raw) > _BARE_KEY_LEN:
        return ""
    keys = {k for k in _KEY_RE.findall(raw) if k.split("-")[0] not in _NOT_PROJECTS}
    return keys.pop() if len(keys) == 1 else ""


def normalise_title(title: str | None) -> frozenset:
    """Lower-case word tokens of *title* without punctuation or numbers."""
    return frozenset(_WORD_RE.findall((title or "").lower()))


def similarity(a: frozenset, b: frozenset) -> float:
    """Token-set similarity (Jaccard) of two normalised titles, 0.0 to 1.0."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def count_overlap(new_titles: list, old_titles: list, threshold: float) -> int:
    """How many *new_titles* have a counterpart in *old_titles* at or above *threshold*."""
    old = [normalise_title(t) for t in old_titles]
    hits = 0
    for title in new_titles:
        tokens = normalise_title(title)
        if any(similarity(tokens, o) >= threshold for o in old):
            hits += 1
    return hits


class Earlier(NamedTuple):
    """One earlier suite of the same source, as listed to the tester."""

    suite_id: str
    version: int
    created_at: float
    case_count: int
    export_path: str
    prep_id: str = ""


def _when(created_at: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(created_at))


def _path_text(path: str) -> str:
    if not path:
        return "(xlsx path not recorded)"
    if not Path(path).exists():
        return f"{path} (xlsx no longer on disk - regenerate or call qa_export_suite)"
    return path


def earlier_line(e: Earlier) -> str:
    """One bullet naming an earlier suite: version, id, time, cases, xlsx path."""
    path = _path_text(e.export_path)
    return (
        f"- v{e.version} `{e.suite_id}` created {_when(e.created_at)}, "
        f"{e.case_count} cases, {path}"
    )


def versions_line(earlier: list, version: int) -> str:
    """The one-line "this is version N, earlier: ..." head for a reply or sheet."""
    names = ", ".join(f"v{e.version} `{e.suite_id}`" for e in earlier)
    return f"This is version {version} of this source's suite. Earlier: {names}."


def overlap_line(n: int, m: int, prev_version: int) -> str:
    """The "N of M cases overlap version X" report."""
    return (
        f"{n} of {m} cases overlap version {prev_version} "
        f"(title similarity >= {OVERLAP_THRESHOLD:.0%})"
    )
