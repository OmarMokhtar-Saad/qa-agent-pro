"""Validation of the host's untrusted verdict payload (leaf of grounding_verdicts)."""

from __future__ import annotations

import logging
import re
from typing import Any

from config.settings_guard import never_raise
from tools.grounding_verdicts_types import VALID_VERDICTS, Verdict

logger = logging.getLogger("tools.grounding_verdicts")

# Shape caps on the untrusted payload.
_MAX_VERDICTS = 500
_MAX_NOTE_CHARS = 300

_WS_RE = re.compile(r"\s+")
_TC_ID_RE = re.compile(r"^TC-\d{1,6}$")


def _clean_note(raw: object) -> str:
    with never_raise():
        return _WS_RE.sub(" ", str(raw or "")).strip()[:_MAX_NOTE_CHARS]
    return ""


def _allowed_ids(valid_tc_ids: Any) -> set[str]:
    try:
        return {str(t) for t in (valid_tc_ids or ())}
    except TypeError:
        return set()


def _entry_verdict(entry: object, allowed: set[str], seen: set[str]) -> Verdict | None:
    if not isinstance(entry, dict):
        return None
    tc_id = str(entry.get("tc_id") or "").strip()
    if not _TC_ID_RE.match(tc_id) or tc_id not in allowed or tc_id in seen:
        return None
    verdict = str(entry.get("verdict") or "").strip().lower()
    if verdict not in VALID_VERDICTS:
        return None
    seen.add(tc_id)
    return Verdict(tc_id=tc_id, verdict=verdict, note=_clean_note(entry.get("note")))


def _verdicts_of(raw: object, allowed: Any) -> list[Verdict]:
    if not isinstance(raw, list) or not allowed:
        return []
    seen: set[str] = set()
    entries = (_entry_verdict(e, allowed, seen) for e in raw[:_MAX_VERDICTS])
    return [v for v in entries if v is not None]


def parse_verdicts(raw: object, valid_tc_ids: Any) -> list[Verdict]:
    """Validated verdicts from the host's untrusted ``grounding_verdicts`` field.

    Accepts ``[{"tc_id": "TC-001", "verdict": "ungrounded", "note": "..."}, ...]``.
    An entry is kept only when its ``tc_id`` is well-formed AND present in
    ``valid_tc_ids`` -- a verdict can never introduce a case -- and its verdict is
    one of :data:`VALID_VERDICTS`. Duplicate ids keep the FIRST entry, so a
    payload cannot flip a decision by repeating itself. Never raises.
    """
    with never_raise(
        logger,
        "parse_verdicts failed - discarding the verdict payload",
        level=logging.ERROR,
        exc_info=True,
    ):
        return _verdicts_of(raw, _allowed_ids(valid_tc_ids))
    return []
