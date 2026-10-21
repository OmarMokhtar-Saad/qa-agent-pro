"""The run manifest: create, write, read, and per-step timing rows.

:func:`append_step_timing` numbers the row after the highest index already
stored and bounds the list to the newest ``step_timing.MAX_STEP_TIMING_ROWS``
rows. A ``row`` that is not a ``StepTiming`` (an unarmed timer gives ``None``)
or a run with no manifest writes nothing. Never raises.
"""

from __future__ import annotations

import logging
import time

from tools.mobile import ime_proof, run_consts, run_goal_intake, run_io, step_timing

logger = logging.getLogger("tools.mobile.run_store")


def create_run(run_id: str, manifest: dict) -> dict:
    """Create ``runs/<run_id>/`` and write its manifest. Idempotent."""
    try:
        if not run_io.valid_run_id(run_id):
            return {
                "error": "Refusing to use " + repr(str(run_id)[:40]) + " as a run id.",
                "content": None,
            }
        root = run_io.run_path(run_id)
        (root / run_consts.CASES_DIR).mkdir(parents=True, exist_ok=True)
        payload = dict(manifest or {})
        run_goal_intake.register_goals(payload, run_id)
        payload.setdefault("run_id", run_id)
        payload.setdefault("created", time.time())
        run_io._write_json(root / run_consts.MANIFEST_FILE, payload)
        return {"error": None, "content": {"run_id": run_id, "path": str(root)}}
    except Exception as exc:
        logger.exception("mobile.run_store.create_run failed")
        return {"error": str(exc), "content": None}


def write_manifest(run_id: str, manifest: dict) -> dict:
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        target = run_io.run_path(run_id) / run_consts.MANIFEST_FILE
        run_io._write_json(target, dict(manifest or {}))
        return {"error": None, "content": {"run_id": run_id}}
    except Exception as exc:
        logger.exception("mobile.run_store.write_manifest failed")
        return {"error": str(exc), "content": None}


def read_manifest(run_id: str) -> dict:
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        target = run_io.run_path(run_id) / run_consts.MANIFEST_FILE
        return {"error": None, "content": run_io._read_json(target)}
    except Exception as exc:
        logger.exception("mobile.run_store.read_manifest failed")
        return {"error": str(exc), "content": None}


def _numbered_rows(manifest: dict, row: step_timing.StepTiming) -> tuple[list, int]:
    stored = [
        parsed
        for parsed in map(
            step_timing.StepTiming.from_dict, manifest.get("step_timings") or []
        )
        if parsed is not None
    ]
    number = max([parsed.index for parsed in stored] + [0]) + 1
    numbered = step_timing.StepTiming(**{**row.to_dict(), "index": number})
    kept, _dropped = step_timing.bound_rows(
        [parsed.to_dict() for parsed in stored] + [numbered.to_dict()]
    )
    return kept, number


# The row is numbered after the highest index already stored and the list is
# bounded to the newest ``step_timing.MAX_STEP_TIMING_ROWS`` rows. A ``row``
# that is not a ``StepTiming`` (an unarmed timer gives ``None``) or a run with
# no manifest writes nothing. Never raises.
def append_step_timing(run_id: str, row: object) -> dict:
    """Append one step's timing row to ``manifest["step_timings"]``."""
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        target = run_io.run_path(run_id) / run_consts.MANIFEST_FILE
        manifest = run_io._read_json(target)
        if not isinstance(row, step_timing.StepTiming) or not isinstance(
            manifest, dict
        ):
            return {"error": None, "content": {"written": False}}
        manifest["step_timings"], number = _numbered_rows(manifest, row)
        manifest["qa_ime"] = ime_proof.evidence(str(manifest.get("serial") or ""))
        run_io._write_json(run_io.run_path(run_id) / run_consts.MANIFEST_FILE, manifest)
        return {"error": None, "content": {"written": True, "index": number}}
    except (OSError, TypeError, ValueError):
        logger.exception("mobile.run_store.append_step_timing failed")
        return {"error": "step timing not recorded", "content": None}
