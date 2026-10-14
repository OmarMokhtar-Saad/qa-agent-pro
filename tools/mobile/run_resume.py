"""Where a resumed run picks up: ``{done, failed, verdicts, next_index}``."""

from __future__ import annotations

import logging

from tools.mobile import run_cases, run_coverage, run_manifest

logger = logging.getLogger("tools.mobile.run_store")


def _verdict_rows(listed: dict) -> tuple[list[str], list[str], dict[str, str]]:
    done: list[str] = []
    failed: list[str] = []
    verdicts: dict[str, str] = {}
    for body in listed.get("content") or []:
        tc_id = str(body.get("tc_id") or "")
        verdict = str(body.get("verdict") or "")
        if not tc_id:
            continue
        verdicts[tc_id] = verdict
        # The SAME tuple report.py renders from and case_runner accepts.
        # It was three separate literals, and `unverified` reached two of
        # them: a case that proved nothing never counted as done, so the
        # scheduler re-served it forever and the run could not finish.
        if verdict in run_coverage.DONE_VERDICTS:
            done.append(tc_id)
        if verdict == "fail":
            failed.append(tc_id)
    return done, failed, verdicts


def _next_index(order: list[str], done: list[str]) -> int:
    for index, tc_id in enumerate(order):
        if tc_id not in done:
            return index
    return len(order)


def _content(listed: dict, manifest: dict) -> dict:
    done, failed, verdicts = _verdict_rows(listed)
    order = [str(x) for x in (manifest.get("order") or [])]
    return {
        "done": sorted(done),
        "failed": sorted(failed),
        "verdicts": verdicts,
        "next_index": _next_index(order, done),
        "total": len(order),
    }


def resume_point(run_id: str) -> dict:
    """``{done, failed, verdicts, next_index}`` for a resumed run."""
    try:
        listed = run_cases.list_cases(run_id)
        if listed.get("error"):
            return listed
        manifest = (run_manifest.read_manifest(run_id) or {}).get("content") or {}
        return {"error": None, "content": _content(listed, manifest)}
    except Exception as exc:
        logger.exception("mobile.run_store.resume_point failed")
        return {"error": str(exc), "content": None}
