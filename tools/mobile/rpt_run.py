from __future__ import annotations

import logging
import os
from pathlib import Path

from tools.mobile import run_store
from tools.mobile.report_shell import report_path
from tools.mobile.report_text import _text
from tools.mobile.report_verdict import (
    DONE_VERDICTS,
    explore_stop,
    tally,
    verdict_coverage,
)
from tools.mobile.rpt_build import _document

logger = logging.getLogger("tools.mobile.report")


def _write_tmp(tmp: Path, page: str) -> None:
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(page)
        handle.flush()
        os.fsync(handle.fileno())


def _write_page(target: Path, page: str) -> dict:
    """tmp + ``os.replace``, so a killed render leaves the previous good file."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        _write_tmp(tmp, page)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, target)
        return {"error": None, "content": {"path": str(target)}}
    except OSError as exc:
        logger.warning("mobile.report: could not write %s: %s", target, exc)
        return {
            "error": "Could not write the report: " + str(exc)[:200],
            "content": None,
        }


def _is_partial(manifest: dict, cases: list, tally: dict) -> bool:
    """Whether this run is still going, decided from the FILES.

    The manifest's ``cases`` key is never consulted -- a run planned by an older
    build may not have one, and the checkpoints are the authority anyway.
    """
    if str(manifest.get("lane") or "") == "explore":
        return not bool(explore_stop(manifest))
    done = sum(int(count) for name, count in tally.items() if name in DONE_VERDICTS)
    planned = 0
    try:
        planned = int(manifest.get("total") or 0)
    except (TypeError, ValueError, OverflowError):
        planned = 0
    return bool(not cases or done < max(planned, len(cases)))


def _read_run(run_id: str) -> dict:
    cases = [
        case
        for case in (run_store.list_cases(run_id) or {}).get("content") or []
        if isinstance(case, dict)
    ]
    screens = (run_store.list_screens(run_id) or {}).get("content") or {}
    # The evidence library, read beside the dedup library and never
    # instead of it: the audit wants one row per screen, a frame wants
    # the look the step actually took.
    observations = (run_store.list_observations(run_id) or {}).get("content") or {}
    lease = (run_store.read_lease(run_id) or {}).get("content") or {}
    lease = lease if isinstance(lease, dict) else {}
    return {
        "cases": cases,
        "screens": screens,
        "observations": observations,
        "lease": lease,
    }


def _facts(cases: list, manifest: dict) -> dict:
    # NOT `tally = tally(cases)`: that binds `tally` as a local for the
    # WHOLE function body, so the call on the right resolves to an unbound
    # local and EVERY invocation raises UnboundLocalError -- which this
    # function's own `except Exception` then reports as a handled error,
    # so the module looks alive and returns nothing. Found by EXECUTING.
    counts = tally(cases)
    partial = _is_partial(manifest, cases, counts)
    coverage = verdict_coverage(cases, manifest)
    return {"tally": counts, "partial": partial, "coverage": coverage}


def _write_result(run_id: str, page: str, fields: dict) -> dict:
    target = report_path(run_id)
    written = _write_page(target, page)
    if written.get("error"):
        return written
    return {
        "error": None,
        "content": {
            "path": str(target),
            "partial": fields["partial"],
            "verdict_coverage": fields["coverage"],
            "cards": len(fields["cases"]),
            "totals": fields["tally"],
            "bytes": len(page.encode("utf-8")),
        },
    }


def _render_run(run_id: str, manifest: dict) -> dict:
    """Read the run's files, build the page, write it; the ``render`` result."""
    files = _read_run(run_id)
    meta = _facts(files["cases"], manifest)
    page = _document(run_id=str(run_id), manifest=manifest, **files, **meta)
    return _write_result(str(run_id), page, {**files, **meta})


def _render_checked(run_id: str) -> dict:
    if not run_store.valid_run_id(run_id):
        return {
            "error": "Refusing " + repr(str(run_id)[:40]) + " as a run id.",
            "content": None,
        }
    manifest = (run_store.read_manifest(run_id) or {}).get("content")
    manifest = manifest if isinstance(manifest, dict) else {}
    if not manifest:
        return {
            "error": (
                "No run `"
                + _text(run_id, 64)
                + "` on this machine, so there is nothing to report."
            ),
            "content": None,
        }
    return _render_run(run_id, manifest)


def render(run_id: str) -> dict:
    """Write ``runs/<run_id>/report/index.html`` and its ``media/`` beside it.

    The artifact is a FOLDER: the page references its recordings and frames
    relatively, so the whole folder zips and opens anywhere. Built from that
    run's files only.

    ``{"error", "content": {"path", "partial", "cards", "totals", "bytes"}}``.
    Never raises.
    """
    try:
        return _render_checked(run_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.report.render failed")
        return {"error": str(exc), "content": None}
