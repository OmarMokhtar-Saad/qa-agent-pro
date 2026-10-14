"""Profile-driven pattern lookup: every stream pattern is read through ``_pat``."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tools.mobile_evidence.grammar_consts import _META, STREAMS


def _looks_like_regex(text: str) -> bool:
    return text.startswith("^") or any(ch in _META for ch in text)


def _literal_prefix(profile) -> str:
    """The literal head every SDK line opens with (``##``), whether the profile gave the
    literal or the whole prefix REGEX: the run of non-meta characters after a leading ``^``."""
    prefix = str(getattr(profile, "log_prefix", "") or "")
    if not _looks_like_regex(prefix):
        return prefix
    head = prefix[1:] if prefix.startswith("^") else prefix
    out = []
    for ch in head:
        if ch in _META:
            break
        out.append(ch)
    return "".join(out)


def _prefix_re(profile) -> re.Pattern:
    """``<prefix> - [CAT]`` -- the category prefix the SDK's logger writes.

    Accepts either a literal prefix (``##``), from which the shape is built, or the
    full prefix regex with a ``cat`` group, used as given.
    """
    prefix = str(getattr(profile, "log_prefix", "") or "")
    if not prefix:
        return re.compile(r"^(?:\[(?P<cat>[A-Z]+)\]\s*)?")
    if _looks_like_regex(prefix):
        try:
            pat = re.compile(prefix)
            if "cat" in pat.groupindex:
                return pat
        except re.error:
            pass
        prefix = _literal_prefix(profile)
        if not prefix:
            return re.compile(r"^(?:\[(?P<cat>[A-Z]+)\]\s*)?")
    return re.compile("^" + re.escape(prefix) + r"\s*-\s*(?:\[(?P<cat>[A-Z]+)\]\s*)?")


def _head_re(profile, compiled) -> re.Pattern:
    """The looser strip for a line whose dash belongs to the message itself."""
    pat = _pat(compiled, "hash_head")
    if pat is not None:
        return pat
    prefix = _literal_prefix(profile)
    if not prefix:
        return re.compile(r"^\s*")
    return re.compile("^" + re.escape(prefix) + r"\s*-?\s*")


def _pat(compiled, name: str):
    """The compiled pattern for one stream, or None when the profile disables it."""
    body = compiled if isinstance(compiled, dict) else {}
    patterns = body.get("patterns") or {}
    return patterns.get(name)


def _match(compiled, name: str, text: str):
    pat = _pat(compiled, name)
    return pat.match(text) if pat is not None else None


def _disabled_pairs(compiled) -> list:
    """The profile's own disabled list as ``[(stream, reason)]``, whichever shape it came in."""
    body = compiled if isinstance(compiled, dict) else {}
    raw = body.get("disabled") or {}
    if isinstance(raw, dict):
        return [(str(k), str(v)) for k, v in raw.items()]
    out = []
    for item in raw:
        try:
            out.append((str(item[0]), str(item[1])))
        except (TypeError, IndexError, KeyError):
            continue
    return out


def _names(value) -> list:
    if isinstance(value, str):
        return [value]
    return [str(v) for v in (value or [])]


def _msg_in(msg: str, names) -> bool:
    """Exact name, or a prefix when the configured name ends with a dot."""
    for name in _names(names):
        if name.endswith(".") and msg.startswith(name):
            return True
        if msg == name:
            return True
    return False


def _disabled_streams(compiled) -> list:
    """The profile's own disabled pairs, then every stream with no pattern."""
    disabled = _disabled_pairs(compiled)
    for name in STREAMS:
        if _pat(compiled, name) is None and not any(d[0] == name for d in disabled):
            disabled.append((name, "no pattern in the profile"))
    return disabled


@dataclass(frozen=True)
class _Grammar:
    """The profile settings ``parse`` reads once, before the first event."""

    profile: object
    compiled: dict
    tool_cat: object
    cost_cat: object
    config_cat: object
    tool_msgs: object
    cost_msgs: object
    config_msgs: object
    bad_marks: list
    head_re: re.Pattern


def _grammar_of(profile, compiled) -> _Grammar:
    """Read the structured categories, run-log marks and head prefix of ``profile``."""
    structured = getattr(profile, "structured", None) or {}
    return _Grammar(
        profile=profile,
        compiled=compiled,
        tool_cat=structured.get("tool_category"),
        cost_cat=structured.get("cost_category"),
        config_cat=structured.get("config_category"),
        tool_msgs=structured.get("tool_msgs") or [],
        cost_msgs=structured.get("cost_msgs") or [],
        config_msgs=structured.get("config_msg") or [],
        bad_marks=_names(getattr(profile, "runlog_bad", None) or []),
        head_re=_head_re(profile, compiled),
    )
