"""Validate reported feedback and weigh it against the screen dump."""

from __future__ import annotations

import logging
import re
from typing import Any

from tools.mobile import app_knowledge
from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_limits as limits
from tools.mobile.knowledge_feedback_core import VERDICTS, Feedback, _text

logger = logging.getLogger(__name__)


def _fields(entry: dict) -> Feedback | None:
    parsed = kdb.parse_item_id(entry.get("item_id"))
    verdict = str(entry.get("verdict") or "").strip().lower()
    if parsed is None or verdict not in VERDICTS:
        return None
    return Feedback(
        item_id="%s:%d" % parsed,
        verdict=verdict,
        observation=_text(entry.get("observation")),
        proposed=_text(entry.get("proposed")),
    )


def _one(entry: Any, problems: list) -> Feedback | None:
    """The clean item for one reported entry, else ``None`` (the reason is appended)."""
    if not isinstance(entry, dict):
        problems.append("item is not an object")
        return None
    fb = _fields(entry)
    if fb is None:
        problems.append(
            "item_id must be <table>:<id> and verdict one of " + "/".join(VERDICTS)
        )
        return None
    reason = app_knowledge.secret_reason(fb["observation"] + "\n" + fb["proposed"])
    if reason:
        problems.append("%s: refused (%s)" % (fb["item_id"], reason))
        return None
    return fb


def validate_checked(raw: Any) -> tuple:
    """``(items, problems)``. Items are clean, at most ``MAX_FEEDBACK_ITEMS``;
    a malformed item or one holding a secret is dropped and named in problems."""
    items: list = []
    problems: list = []
    try:
        listing = (
            raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
        )
        for entry in listing[: limits.MAX_FEEDBACK_ITEMS]:
            fb = _one(entry, problems)
            if fb is not None:
                items.append(fb)
    except Exception:
        logger.exception("knowledge feedback validate failed")
    return items, problems


def validate(raw: Any) -> list:
    """The clean feedback items in *raw* (never raises, never more than the cap)."""
    return validate_checked(raw)[0]


def _rids(screen: Any) -> list:
    rids: list = []
    try:
        for element in (screen or {}).get("elements") or []:
            if isinstance(element, dict) and element.get("rid"):
                rids.append(str(element["rid"]))
    except Exception:
        logger.exception("knowledge feedback: screen unreadable")
    return rids


def verify(fb: Any, screen: Any) -> float:
    """1.0 when the proposal is checkable against the dump (a proposed resource id
    is on screen), else ``HALF_WEIGHT``."""
    try:
        proposed = str((fb or {}).get("proposed") or "")
        have = _rids(screen)
        for token in re.findall(r"[\w.:/-]{3,}", proposed):
            if any(app_knowledge.rid_matches(r, token) for r in have):
                return 1.0
    except Exception:
        logger.exception("knowledge feedback verify failed")
    return limits.HALF_WEIGHT
