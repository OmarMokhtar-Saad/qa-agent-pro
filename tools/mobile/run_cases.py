"""Per-case checkpoints: ``runs/<run_id>/cases/<tc_id>.json``.

:func:`write_case` checkpoints one case. The payload is redacted before it
touches disk. :func:`list_cases` returns every checkpointed case body, ordered
by tc_id.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from tools.mobile import media_pending, run_consts, run_io

logger = logging.getLogger("tools.mobile.run_store")


def _case_refusal(run_id: str, tc_id: str) -> dict | None:
    if not run_io.valid_run_id(run_id):
        return {"error": "Invalid run id.", "content": None}
    if not run_io.valid_tc_id(tc_id):
        return {
            "error": (
                "Refusing to checkpoint case id "
                + repr(str(tc_id)[:40])
                + "; it must look like TC-001."
            ),
            "content": None,
        }
    return None


def _case_file(run_id: str, tc_id: str) -> Path:
    return run_io.run_path(run_id) / run_consts.CASES_DIR / (tc_id + ".json")


def _write_draining(run_id: str, tc_id: str, body: dict) -> None:
    """Index media produced before this case had a record, then write the case.

    The held media is taken before the write; if the write fails it is put back.
    """
    late = media_pending.take(run_id, tc_id)
    if late:
        body["media"] = [*list(body.get("media") or []), *late]
    try:
        run_io._write_json(_case_file(run_id, tc_id), body)
    except BaseException:
        media_pending.put_back(run_id, tc_id, late)
        raise


def write_case(run_id: str, tc_id: str, payload: dict) -> dict:
    """Checkpoint one case. The payload is redacted before it touches disk."""
    try:
        refusal = _case_refusal(run_id, tc_id)
        if refusal is not None:
            return refusal
        body = dict(payload or {})
        body.setdefault("tc_id", tc_id)
        body.setdefault("updated", time.time())
        _write_draining(run_id, tc_id, body)
        return {"error": None, "content": {"tc_id": tc_id}}
    except Exception as exc:
        logger.exception("mobile.run_store.write_case failed")
        return {"error": str(exc), "content": None}


def read_case(run_id: str, tc_id: str) -> dict:
    try:
        if not run_io.valid_run_id(run_id) or not run_io.valid_tc_id(tc_id):
            return {"error": "Invalid run or case id.", "content": None}
        return {"error": None, "content": run_io._read_json(_case_file(run_id, tc_id))}
    except Exception as exc:
        logger.exception("mobile.run_store.read_case failed")
        return {"error": str(exc), "content": None}


def list_cases(run_id: str) -> dict:
    """Every checkpointed case body, ordered by tc_id."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        directory = run_io.run_path(run_id) / run_consts.CASES_DIR
        bodies: list[dict] = []
        if directory.is_dir():
            for child in sorted(directory.glob("TC-*.json")):
                body = run_io._read_json(child)
                if isinstance(body, dict):
                    bodies.append(body)
        return {"error": None, "content": bodies}
    except Exception as exc:
        logger.exception("mobile.run_store.list_cases failed")
        return {"error": str(exc), "content": None}
