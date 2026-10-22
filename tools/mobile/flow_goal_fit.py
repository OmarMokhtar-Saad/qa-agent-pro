"""How well a saved flow's goal fits the run's goal (ranks the one flow the model is offered).

Word-stem overlap (stop words ignored, ``log in`` = ``sign in`` = ``login``), any language that splits on spaces: no app or label is named here.
"""

from __future__ import annotations

import re

from tools.mobile import run_store

_STEM = 5
_MIN_WORD = 3
_BUCKETS = 5


#: Words that say nothing about the goal; they never count toward an overlap.
STOP_WORDS = frozenset(
    "the and for with from that this then into onto when are was has have not any all "
    "can should must will its your you our their".split()
)
#: ``log in`` / ``sign-in`` / ``signin`` / ``log on`` all mean one thing.
_LOGIN = re.compile(r"\b(?:log|sign)[\s_-]?(?:in|on)\b")
_LOGOUT = re.compile(r"\b(?:log|sign)[\s_-]?out\b")


def _canon(text: object) -> str:
    low = str(text or "").lower()
    return _LOGOUT.sub("logout", _LOGIN.sub("login", low))


def _stems(text: object) -> frozenset:
    words = re.findall(r"\w+", _canon(text))
    return frozenset(
        w[:_STEM] for w in words if len(w) >= _MIN_WORD and w not in STOP_WORDS
    )


def has_stems(text: object) -> bool:
    """False when *text* has no meaningful word (only stop words or short words)."""
    return bool(_stems(text))


def fit(run_goal: object, flow_goal: object) -> int:
    """0.._BUCKETS: the share of the flow goal's word stems the run goal also has."""
    want, have = _stems(flow_goal), _stems(run_goal)
    if not want or not have:
        return 0
    return int(_BUCKETS * len(want & have) / len(want))


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def run_goal(run_id: object) -> str:
    """The charter goal of the run, or ``""`` (never raises)."""
    try:
        got = _dict(run_store.read_manifest(str(run_id or "")))
        explore = _dict(_dict(got.get("content")).get("explore"))
        charter = _dict(explore.get("charter"))
        return str(explore.get("goal") or charter.get("goal") or "")
    except (OSError, ValueError):
        return ""
