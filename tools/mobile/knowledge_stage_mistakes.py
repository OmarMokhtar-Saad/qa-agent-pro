"""Learning stage mistakes: failure signatures, the credit/fix contrast, field binding.

From the run's ``StepRec`` list (never screen text, never typed values) this stage
derives deterministic failure signatures and upserts ``mistakes`` rows, and learns
which field an element takes from a successful ``type`` (``elements.bound_field``).
Credit goes to the first non-ok step of a failure episode plus its predecessor; the
fix is the later OK retry on the same screen_key (the contrast). A mistake is a
candidate until the gate sees MISTAKE_ACTIVE_RUNS distinct runs; a tester
correction is trusted at once. Text is built from templates and rid/field NAMES only.
A degraded screen key (no stable anchors) learns nothing.
"""

from __future__ import annotations

import json  # noqa: F401

from tools.mobile import knowledge_db, knowledge_limits, screen_key  # noqa: F401
from tools.mobile.knowledge_learn import StageResult
from tools.mobile.kstg_mist_bind import BindSlot, bind, learned_bindings
from tools.mobile.kstg_mist_rows import credit as _credit
from tools.mobile.kstg_mist_sig import (  # noqa: F401
    _NO_TARGET,
    _OUTCOME_SIGNATURE,
    _SYMPTOM,
    OK_OUTCOMES,
    TAP_OPS,
    degraded,
    find_hits,
    signature_of,
    texts,
)


def run(conn, lc) -> StageResult:
    """Never raises past the pipeline; one transaction per call (the pipeline's)."""
    steps = [
        s for s in (lc.steps or []) if not degraded(str(s.get("screen_key") or ""))
    ]
    result = StageResult("mistakes")
    for hit in find_hits(steps):
        result.wrote += _credit(conn, hit, lc.run_id)
    for slot, val in learned_bindings(steps).items():
        result.wrote += bind(conn, BindSlot(slot, val, lc.run_id))
    result.notes.append("%d mistake/binding row(s)" % result.wrote)
    return result
