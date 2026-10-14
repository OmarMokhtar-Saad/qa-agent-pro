"""Host mode: the staged/missing/ready view behind qa_prep_status."""

from __future__ import annotations

import logging

from agents.host_category_consts import logger
from agents.host_category_names import normalize_category
from config.settings_guard import never_raise


def _stage(staged_raw_names: list) -> tuple:
    """``(staged, unrecognized, seen)`` from the raw staged names, normalized and de-duplicated."""
    staged: list = []
    unrecognized: list = []
    seen: set = set()
    for raw in staged_raw_names or []:
        canon = normalize_category(raw)
        if not canon:
            if raw and str(raw) not in unrecognized:
                unrecognized.append(str(raw))
            continue
        if canon in seen:
            continue
        seen.add(canon)
        staged.append(canon)
    return staged, unrecognized, seen


def _status(expected_list: list, found: tuple) -> dict:
    """The status dict for ``expected_list`` and ``found`` = ``(staged, unrecognized, seen)``."""
    staged, unrecognized, seen = found
    missing = [n for n in expected_list if n not in seen]
    ready = bool(expected_list) and not missing and set(staged) >= set(expected_list)
    return {
        "expected": expected_list,
        "staged": staged,
        "missing": missing,
        "unrecognized": unrecognized,
        "ready": ready,
        "staged_count": len(staged),
        "expected_count": len(expected_list),
    }


def _empty_status() -> dict:
    """The all-empty status returned when the view itself failed."""
    return {
        "expected": [],
        "staged": [],
        "missing": [],
        "unrecognized": [],
        "ready": False,
        "staged_count": 0,
        "expected_count": 0,
    }


def prep_status_view(*, expected: list, staged_raw_names: list) -> dict:
    """Compute staged/missing/ready for qa_prep_status. Pure; never raises.

    Staged names are normalized; unknown aliases that normalize to "" are listed
    under unrecognized and do not count toward ready.
    """
    with never_raise(
        logger, "prep_status_view failed", level=logging.WARNING, exc_info=True
    ):
        expected_list = [str(x) for x in (expected or []) if str(x).strip()]
        return _status(expected_list, _stage(staged_raw_names))
    return _empty_status()
