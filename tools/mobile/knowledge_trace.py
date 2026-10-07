"""Trace input for the learning stages: one ``StepRec`` per replayed step.

The executor stamps ``screen_key`` / ``after_screen_key`` / ``activity`` on each
step entry in memory; they travel into the case checkpoint with the trace, so
``load_steps`` reads them back from the run folder. A run made before that
stamping yields ``[]`` (nothing is learned from it), never a guess.

Never raises. Screen text and typed values never enter a record.
"""

from __future__ import annotations

import logging
from typing import TypedDict

from tools.mobile import knowledge_limits, run_store

logger = logging.getLogger(__name__)


class StepRec(TypedDict, total=False):
    index: int
    op: str
    target_rid: str
    text_field: str
    screen_key: str
    after_screen_key: str
    activity: str
    outcome: str
    ms: int
    retries: int
    landed: bool | None
    locator_used: str
    locator_tried: list
    popup: str
    tester_correction: bool
    fired: list


def _text(value: object, limit: int = 200) -> str:
    return str(value or "")[:limit]


def _rid(action: dict) -> str:
    rid = action.get("rid")
    if not rid and isinstance(action.get("target"), dict):
        rid = action["target"].get("rid")
    return _text(rid)


def _int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def step_rec(entry: object) -> StepRec | None:
    """One trace entry as a ``StepRec``; ``None`` when it was not stamped."""
    if not isinstance(entry, dict) or not entry.get("screen_key"):
        return None
    action = entry.get("action") if isinstance(entry.get("action"), dict) else {}
    landed = entry.get("landed")
    return StepRec(
        index=_int(entry.get("index")),
        op=_text(action.get("op"), 40),
        target_rid=_rid(action),
        text_field=_text(entry.get("text_field") or action.get("field"), 80),
        screen_key=_text(entry.get("screen_key"), 64),
        after_screen_key=_text(entry.get("after_screen_key"), 64),
        activity=_text(entry.get("activity"), 200),
        outcome=_text(entry.get("outcome"), 40),
        ms=_int(entry.get("ms")),
        retries=_int(entry.get("retries")),
        landed=landed if isinstance(landed, bool) else None,
        locator_used=_text(entry.get("locator_used"), 200),
        locator_tried=list(entry.get("locator_tried") or [])[:10],
        popup=_text(entry.get("popup"), 80),
        tester_correction=bool(entry.get("tester_correction")),
        fired=list(entry.get("knowledge") or entry.get("fired") or [])[:10],
    )


def load_steps(run_id: str) -> list[StepRec]:
    """Every stamped step of *run_id*, in order, at most ``LEARN_MAX_STEPS``."""
    out: list[StepRec] = []
    try:
        listed = run_store.list_cases(str(run_id or ""))
        for case in list(listed.get("content") or []):
            for entry in list((case or {}).get("trace") or []):
                rec = step_rec(entry)
                if rec is not None:
                    out.append(rec)
                if len(out) >= knowledge_limits.LEARN_MAX_STEPS:
                    return out
    except Exception:
        logger.exception("knowledge trace: load failed for %s", str(run_id)[:64])
    return out
