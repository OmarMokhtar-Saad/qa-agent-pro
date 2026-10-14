"""Mobile session: the identity of "this app, these cases" (cut from session_runs.py, C5 w17)."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from tools.mobile import run_store

logger = logging.getLogger("tools.mobile.session")


def _body_of(case: object) -> dict | None:
    """One case as a plain dict, or None when it cannot be read as one."""
    try:
        body = case.model_dump(mode="json") if hasattr(case, "model_dump") else case
    except (ValueError, TypeError, AttributeError):  # pragma: no cover
        return None
    return body if isinstance(body, dict) else None


def _step_text(step: dict) -> str:
    """One step as the text that goes into the signature."""
    return (
        str(step.get("action") or "")
        + "\u0000"
        + str(step.get("test_data") or "")
        + "\u0000"
        + str(step.get("expected_result") or "")
    )


# THE STEPS TOO, not only the id and the title. Hashing the name alone
# meant a tester who EDITED TC-001's steps and re-submitted was offered
# the previous run -- which then executed the old steps and reported
# them under the new case's name. A silently wrong result is worse than
# a duplicate run, which is all this signature exists to avoid.
def _case_line(body: dict) -> str:
    """One case as the text that goes into the signature."""
    steps = [_step_text(s) for s in body.get("steps") or [] if isinstance(s, dict)]
    return (
        str(body.get("tc_id") or "")
        + "\u0000"
        + str(body.get("title") or "")
        + "\u0000"
        + "\u0003".join(steps)
    )


# Derived from the package and the SORTED (tc_id, title) pairs, so the order
# the cases were pasted in does not make a second run look like a new one,
# and a genuinely different set never collides.
#
# It exists because on 2026-09-04 a session started EIGHT runs of one case in
# seventeen minutes: nothing could tell the model that the run it had just
# abandoned was the one to go back to, so it planned another.
def case_signature(package: object, cases: Any) -> str:
    """An identity for "this app, these cases", stable across chats."""
    bodies = [_body_of(case) for case in list(cases or [])]
    pairs = [_case_line(body) for body in bodies if body is not None]
    seed = str(package or "") + "\u0001" + "\u0002".join(sorted(pairs))
    return hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:16]


def run_signature(run_id: str) -> str:
    """The signature recorded on a run's manifest, or ``""``."""
    manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
    return str(manifest.get("case_signature") or "")
