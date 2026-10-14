"""The CURRENT-CASE pointer the addon reads, and the per-case raw JSONL paths.

:func:`mark_case_current` / :func:`mark_case_finished` are the ONE producer of
the pointer (see ``flows.py`` for the routing rationale).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from tools.mobile_capture import paths

logger = logging.getLogger("tools.mobile_capture.flows")


def _pointer_path(run_id: str) -> Path:
    return paths.tmp_dir() / (paths.safe_name(run_id) + ".current")


def _jsonl_path(run_id: str, case_key: str) -> Path:
    return paths.tmp_dir() / (
        paths.safe_name(run_id) + "-" + paths.safe_name(case_key) + ".jsonl"
    )


def mark_case_current(run_id: str, tc_id: str) -> dict:
    """Point the addon at *tc_id*'s file. ``{"error", "content"}``, never raises."""
    try:
        target = _pointer_path(run_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(paths.safe_name(tc_id), encoding="utf-8")
        os.replace(tmp, target)
        return {"error": None, "content": {"tc_id": str(tc_id)}}
    except Exception as exc:
        logger.exception("mobile_capture.flows.mark_case_current failed")
        return {"error": str(exc), "content": None}


def mark_case_finished(run_id: str) -> dict:
    """Clear the pointer, so flows from now on land in the outside bucket.

    ``{"error", "content"}``, never raises. A missing pointer is success: the
    end state -- "no case is current" -- already holds.
    """
    try:
        _pointer_path(run_id).unlink(missing_ok=True)
        return {"error": None, "content": {}}
    except Exception as exc:
        logger.exception("mobile_capture.flows.mark_case_finished failed")
        return {"error": str(exc), "content": None}
