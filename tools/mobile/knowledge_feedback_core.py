"""Feedback vocabulary: verdicts, the event name, the item shape and text helpers."""

from __future__ import annotations

import re
from typing import Any, NamedTuple, TypedDict

from tools.mobile import knowledge_limits as limits
from tools.untrusted import single_line, wrap_untrusted

VERDICTS = ("wrong", "outdated", "refine")
EVENT = "feedback"
_CONTEXT_WORDS = (
    ("logged_out", re.compile(r"\blogged[ _-]?out\b|\bsigned[ _-]?out\b", re.I)),
    ("logged_in", re.compile(r"\blogged[ _-]?in\b|\bsigned[ _-]?in\b", re.I)),
    ("keyboard", re.compile(r"\bkeyboard\b", re.I)),
    ("tablet", re.compile(r"\btablet\b", re.I)),
)
_ENV_PAIR = re.compile(r"\b([A-Za-z][\w-]{0,31})=([\w.-]{1,40})\b")
_IGNORED = frozenset({"item_id", "verdict", "observation", "proposed"})


class Feedback(TypedDict, total=False):
    item_id: str
    verdict: str
    observation: str
    proposed: str


class Anchor(NamedTuple):
    """Where a feedback item was reported: the screen dump and the step index."""

    screen: Any = None
    step: int = -1


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())[: limits.FEEDBACK_CHARS]


def context_of(observation: Any) -> dict:
    """The context an observation names, as a narrower ``when.env``: explicit
    ``name=value`` pairs, else one of logged in/out, keyboard, tablet."""
    text = str(observation or "")
    env = {
        k.lower(): v for k, v in _ENV_PAIR.findall(text) if k.lower() not in _IGNORED
    }
    if env:
        return dict(list(env.items())[: limits.ENV_ENTRIES])
    for name, pattern in _CONTEXT_WORDS:
        if pattern.search(text):
            return {"context": name}
    return {}


def describe(fb: Any) -> str:
    """A short plain line (no stored text beyond a clipped observation)."""
    data = fb if isinstance(fb, dict) else {}
    return "%s %s: %s" % (
        data.get("item_id"),
        data.get("verdict"),
        single_line(data.get("observation"), 80),
    )


def echo(fb: Any) -> str:
    """One feedback item as UNTRUSTED text."""
    try:
        data = fb if isinstance(fb, dict) else {}
        body = "%s %s: %s -> %s" % (
            data.get("item_id", ""),
            data.get("verdict", ""),
            data.get("observation", ""),
            data.get("proposed", ""),
        )
        return wrap_untrusted(
            "knowledge feedback", body, limit=2 * limits.FEEDBACK_CHARS + 100
        )
    except (TypeError, ValueError, AttributeError):
        return ""
