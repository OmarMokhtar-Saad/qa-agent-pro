"""Mobile session: cases checkpointed on a run's manifest and read back (cut from session_runs.py, C5 w17)."""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile import run_store
from tools.models import TestCase

logger = logging.getLogger("tools.mobile.session")


def _case_dumps(cases: Any) -> list[dict]:
    """Case bodies for the manifest, so a fresh chat can rehydrate them.

    ``scheduler.plan_run`` writes only the ORDER, and nothing in Phases 1-2
    writes the cases themselves -- so a resumed chat would hold a tc_id it could
    not run. Three of the six start-menu sources never produce a stored suite
    (pasted markdown, csv, xlsx) and an exploratory run has none at all, so
    re-loading from ``suite_store`` was not an option.
    """
    out: list[dict] = []
    for case in list(cases or []):
        try:
            out.append(case.model_dump(mode="json"))
        except (ValueError, TypeError, AttributeError):
            logger.warning("mobile.session: uncheckpointable case skipped")
    return out


def _manifest_cases(run_id: str) -> list:
    """The case bodies stored on a run's manifest."""
    manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
    return list(manifest.get("cases") or [])


def _invalid_case(run_id: str, tc_id: str, exc: Exception) -> dict:
    """The refusal for a stored case that no longer validates."""
    return {
        "error": (
            "Case "
            + str(tc_id)
            + " in run "
            + str(run_id)
            + " no longer validates ("
            + str(exc)[:200]
            + "), so it was not run."
        ),
        "content": None,
    }


def _missing_case(run_id: str, tc_id: str) -> dict:
    """The refusal for a tc_id the run's manifest does not hold."""
    return {
        "error": (
            "Run "
            + str(run_id)
            + " has no case "
            + str(tc_id)
            + " on disk, so there is nothing to run."
        ),
        "content": None,
    }


# This is the other half of :func:`_case_dumps`: without it a resumed chat
# holds a tc_id and nothing to plan against.
def load_case(run_id: str, tc_id: str) -> dict:
    """Rehydrate ONE ``TestCase`` from the run manifest."""
    try:
        for body in _manifest_cases(run_id):
            if not isinstance(body, dict):
                continue
            if str(body.get("tc_id") or "") != str(tc_id):
                continue
            try:
                return {"error": None, "content": TestCase(**body)}
            except (ValueError, TypeError) as exc:
                return _invalid_case(run_id, tc_id, exc)
        return _missing_case(run_id, tc_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.load_case failed")
        return {"error": str(exc), "content": None}


def _rehydrate(run_id: str, bodies: list) -> list:
    """Every stored body that still validates, as a ``TestCase``."""

    out: list = []
    for body in bodies:
        if not isinstance(body, dict):
            continue
        try:
            out.append(TestCase(**body))
        except (ValueError, TypeError):
            logger.warning("mobile.session: case in %s no longer validates", run_id)
    return out


# Re-running the failures of a previous run needs that run's own cases, and
# the previous run is the only place they exist -- the suite it came from may
# never have been stored (a pasted markdown table is not a suite).
def load_run_cases(run_id: str) -> dict:
    """EVERY case of a run, rehydrated. Used by the re-run-failures source."""
    try:
        out = _rehydrate(run_id, _manifest_cases(run_id))
        if not out:
            return {
                "error": ("Run " + str(run_id) + " has no cases on disk to re-run."),
                "content": None,
            }
        return {"error": None, "content": out}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.load_run_cases failed")
        return {"error": str(exc), "content": None}
