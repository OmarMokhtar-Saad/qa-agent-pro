"""Async bodies for ``tools.suite_store`` (never-raises result dicts).

Each takes the facade's ``run`` callable (``run(fn, *args)`` opens a connection,
calls ``fn(conn, *args)`` and closes it) so the facade's ``_connect`` stays the
single seam tests patch.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from tools import (
    suite_store_family,
    suite_store_load,
    suite_store_query,
    suite_store_write,
)
from tools.models import TestSuite
from tools.suite_store_write import SuiteMeta

logger = logging.getLogger("tools.suite_store")

Run = Callable[..., Any]


async def save_suite(run: Run, suite: TestSuite, meta: SuiteMeta) -> dict:
    try:
        suite_id = await asyncio.to_thread(run, suite_store_write.save, suite, meta)
        logger.info(
            "suite_store: saved suite %s (%d cases)", suite_id, len(suite.test_cases)
        )
        return {"error": None, "content": {"suite_id": suite_id}}
    except Exception as exc:
        logger.exception("suite_store.save_suite failed")
        return {"error": str(exc), "content": None}


# F21: say WHICH kind of nothing this is. `exists` True with a
# non-zero `row_total` means the suite IS on disk and every one
# of its rows failed to load -- reporting that as 'no stored
# suite, generate one first' told the tester their cases were
# gone when they were not.
async def _absent_report(run: Run, suite_id: str) -> dict:
    stats = await asyncio.to_thread(run, suite_store_load.case_row_stats, suite_id)
    exists, row_total = stats
    return {
        "error": None,
        "content": None,
        "skipped": row_total if exists else 0,
        "exists": bool(exists),
        "row_total": int(row_total),
    }


async def load_suite(run: Run, suite_id: str) -> dict:
    try:
        if not suite_id:
            return {"error": None, "content": None}
        suite = await asyncio.to_thread(run, suite_store_load.load_suite, suite_id)
        if suite is None:
            return await _absent_report(run, suite_id)
        # Carry the shortfall OUT of the loader so a caller can disclose it;
        # silently exporting fewer cases than were stored is the failure mode.
        return {
            "error": None,
            "content": suite,
            "skipped": int(getattr(suite, "_load_skipped", 0) or 0),
        }
    except Exception as exc:
        logger.exception("suite_store.load_suite failed")
        return {"error": str(exc), "content": None}


async def load_suite_meta(run: Run, suite_id: str) -> dict:
    try:
        if not suite_id:
            return {"error": None, "content": None}
        row = await asyncio.to_thread(run, suite_store_query.load_suite_meta, suite_id)
        return {"error": None, "content": row}
    except Exception as exc:
        logger.exception("suite_store.load_suite_meta failed")
        return {"error": str(exc), "content": None}


async def list_recent_suites(run: Run, limit: int) -> dict:
    try:
        fn = suite_store_query.list_recent
        rows = await asyncio.to_thread(run, fn, max(1, int(limit)))
        return {"error": None, "content": rows}
    except Exception as exc:
        logger.exception("suite_store.list_recent_suites failed")
        return {"error": str(exc), "content": None}


async def find_recent_suite_by_source(
    run: Run, source_url: str, window_s: float | None
) -> dict:
    try:
        if not source_url:
            return {"error": None, "content": None}
        fn = suite_store_query.find_recent_by_source
        row = await asyncio.to_thread(run, fn, source_url, window_s)
        return {"error": None, "content": row}
    except Exception as exc:
        logger.exception("suite_store.find_recent_suite_by_source failed")
        return {"error": str(exc), "content": None}


async def save_checklist(run: Run, suite_id: str, payload: dict) -> dict:
    try:
        if not suite_id:
            return {"error": "suite_id is required", "content": None}
        fn = suite_store_write.save_checklist
        await asyncio.to_thread(run, fn, suite_id, payload or {})
        logger.info(
            "suite_store: saved checklist for suite %s (%d item(s))",
            suite_id,
            len((payload or {}).get("items") or []),
        )
        return {"error": None, "content": {"suite_id": suite_id}}
    except Exception as exc:
        logger.exception("suite_store.save_checklist failed")
        return {"error": str(exc), "content": None}


async def load_checklist(run: Run, suite_id: str) -> dict:
    try:
        if not suite_id:
            return {"error": None, "content": None}
        fn = suite_store_query.load_checklist
        payload = await asyncio.to_thread(run, fn, suite_id)
        return {"error": None, "content": payload}
    except Exception as exc:
        logger.exception("suite_store.load_checklist failed")
        return {"error": str(exc), "content": None}


async def earlier_suites(run: Run, source: str, skip_id: str) -> list:
    """Earlier suites of *source* (oldest first); [] on any failure."""
    try:
        fn = suite_store_family.earlier_suites
        return await asyncio.to_thread(run, fn, source, skip_id)
    except Exception:
        logger.exception("suite_store.earlier_suites failed")
        return []


async def family_of(run: Run, suite_id: str) -> dict | None:
    """Version stamp of a stored suite, or None."""
    try:
        return await asyncio.to_thread(run, suite_store_family.family_of, suite_id)
    except Exception:
        logger.exception("suite_store.family_of failed")
        return None


async def set_export_path(run: Run, suite_id: str, path: str) -> None:
    """Record where a suite's workbook was written; never raises."""
    try:
        await asyncio.to_thread(run, suite_store_family.set_export_path, suite_id, path)
    except Exception:
        logger.exception("suite_store.set_export_path failed")
