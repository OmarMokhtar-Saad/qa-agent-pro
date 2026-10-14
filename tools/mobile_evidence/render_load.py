"""Loading a run's app evidence: the one bundle every fragment takes.

``load_run_evidence`` arms the learned-value net (plan D4); ``release`` drops it.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple, cast

from tools.mobile_evidence import evidence, grammar, profiles
from tools.mobile_evidence.render_read import (
    Run,
    Sources,
    evidence_files,
    flatten_case,
    none_content,
    read_sources,
)
from tools.mobile_evidence.render_view import case_view
from tools.mobile_shared import scrub

logger = logging.getLogger(__name__)


class Built(NamedTuple):
    """The built evidence and the cases flattened for the join."""

    ev: Any
    flat_cases: list


def _profile_name(profile: object) -> str:
    return getattr(profile, "name", "") or getattr(profile, "package", "")


def _profile_fields(profile: object) -> dict:
    return {
        "profile": _profile_name(profile),
        "rates": dict(getattr(profile, "rates", None) or {}),
        "rates_note": str(getattr(profile, "rates_note", "") or ""),
        "env_by_host": dict(getattr(profile, "env_by_host", None) or {}),
    }


def _compiled(run: Run) -> dict | None:
    compiled = profiles.compile(cast("Any", run.profile))
    content = compiled.get("content") if isinstance(compiled, dict) else None
    return content if isinstance(content, dict) else None


def _build_evidence(
    run: Run, flat_cases: list, src: Sources
) -> tuple[Any, dict | None]:
    """(evidence, None) on success, else (None, the refusal)."""
    compiled = _compiled(run)
    if compiled is None:
        return None, none_content("the profile could not be compiled", run.cases)
    inputs = evidence.BuildInputs(
        ndjson=src.ndjson.splitlines() if src.ndjson is not None else None,
        logcat=src.logcat or None,
    )
    kit = grammar.GrammarKit(run.profile, compiled)
    built = evidence.build(flat_cases, kit, inputs)
    return _checked(built, run)


def _checked(built: Any, run: Run) -> tuple[Any, dict | None]:
    ev = built.get("content") if isinstance(built, dict) else None
    if built.get("error") or ev is None:
        return None, none_content(
            "the app log could not be read: " + str(built.get("error") or "unknown"),
            run.cases,
        )
    return ev, None


def _loaded_content(ev: Any, head: tuple, skipped: list) -> dict:
    """The evidence-derived part; ``head`` is ``(source, reason)``."""
    integrity = ev.integrity()
    return {
        "evidence": ev,
        "report": ev.report,
        "source": head[0],
        "reason": head[1],
        "disabled": list(integrity.get("disabledStreams") or []),
        "integrity": integrity,
        "by_case": {},
        "clock_missing": list(integrity.get("clockNotRead") or []),
        "truncated_load": bool(skipped),
        "skipped_slices": list(skipped),
    }


def _head(manifest: dict, src: Sources) -> tuple:
    if src.ndjson is not None:
        return "ndjson", ""
    reason = manifest.get("events_reason") or "the app's own event log was not pulled"
    return "logcat-only", str(reason)


def _assemble(run: Run, built: Built, src: Sources) -> dict:
    scrub.learn_sensitive(built.ev.report)
    content = _loaded_content(built.ev, _head(run.manifest, src), src.skipped)
    content.update(_profile_fields(run.profile))
    content["cases_by_id"] = {str(c.get("tc_id") or ""): c for c in run.cases}
    content["flat_cases"] = built.flat_cases
    for c in built.flat_cases:
        content["by_case"][str(c.get("tc_id") or "")] = case_view(content, c)
    return {"error": None, "content": content}


def _no_log(run: Run) -> dict:
    reason = str(run.manifest.get("events_reason") or "")
    return none_content(
        "no app log captured for this run"
        + ((": " + reason) if reason else " -- no slice and no event log on disk"),
        run.cases,
        {"profile": _profile_name(run.profile)},
    )


def _load(run: Run) -> dict:
    files, refusal = evidence_files(run)
    if refusal is not None:
        return refusal
    src = read_sources(run.run_id, files)
    if src.ndjson is None and not src.logcat:
        return _no_log(run)
    flat_cases = [flatten_case(c) for c in run.cases]
    ev, refusal = _build_evidence(run, flat_cases, src)
    if refusal is not None:
        return refusal
    return _assemble(run, Built(ev, flat_cases), src)


def load_run_evidence(run: Run) -> dict:
    """Read this run's ``evidence/`` files, parse them with its profile, join them to its cases.

    ``{"error": None, "content": {"evidence", "report", "source", "reason", "disabled",
    "integrity", ...}}``. ``source`` is ``"ndjson"`` (the app's own log was pulled),
    ``"logcat-only"`` (slices only) or ``"none"`` (nothing usable, with the reason).
    Never raises; arms the learned-value net on success (release it with :func:`release`).
    """
    cases = [c for c in (run.cases or []) if isinstance(c, dict)]
    scrub.forget_sensitive()
    try:
        manifest = run.manifest if isinstance(run.manifest, dict) else {}
        return _load(Run(run.run_id, manifest, cases, run.profile))
    except Exception as exc:  # noqa: BLE001 - the contract is never to raise
        logger.exception("mobile_evidence.render.load_run_evidence failed")
        return none_content(
            "the app evidence could not be loaded: " + str(exc)[:200], cases
        )


def release() -> None:
    """Drop the learned values once the page is built."""
    scrub.forget_sensitive()
