"""Model-free oracles: what the SERVER can see without asking anyone.

Two detectors today, both region-agnostic:

* **crash/ANR** -- CONSUMES the record ``tools/mobile_evidence/crash_detector``
  already produces (``scan`` / ``crash_of_case``, already wired into the case
  runner and the report). It NEVER re-scans logcat. A second producer of one
  fact is how two answers to "did this crash" get into one report, and the
  second one is always the one nobody is looking at.
* **inert screen** -- the ops that ran, were meant to actuate, and moved
  nothing. ``inert`` arrives as an ARGUMENT (the caller passes
  ``executor.inert_ops(trace)``): importing the executor here would close an
  import cycle, and the executor already owns that derivation.

No model runs here, and none may: a crash is a fact, and a fact judged by a
model is a fact that can be argued with.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Findings returned for ONE turn. The bound is the READER, not the device: a
#: finding is a paragraph a tester reads and a bug report they may file, so a
#: turn that emits more than a screenful has stopped informing anyone. Two
#: detectors can emit at most a handful today; the headroom is for the
#: detectors steps 4+ add without re-deciding this number.
MAX_FINDINGS = 50

KIND_CRASH = "crash"
KIND_INERT = "inert_screen"

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"


def findings(
    crash: object,
    inert: object,
    screen_id: str = "",
    prefix: object = None,
) -> list:
    """Every finding this turn's evidence supports. Never raises.

    *crash* is the record ``crash_detector.crash_of_case`` returns (or ``{}``).
    *inert* is ``executor.inert_ops(trace)``. *prefix* is the action trace that
    reaches this screen -- what turns a finding into a bug report with real
    steps rather than a sentence about a screenshot.
    """
    out: list = []
    try:
        reproduction = list(prefix) if isinstance(prefix, (list, tuple)) else []
        body = crash if isinstance(crash, dict) else {}
        if body.get("detected"):
            out.append(
                {
                    "kind": KIND_CRASH,
                    "severity": SEVERITY_HIGH,
                    "screen_id": str(screen_id or ""),
                    "evidence_refs": [str(body.get("marker") or "")],
                    "reproduction_prefix": reproduction,
                }
            )
        ops = [str(op) for op in inert] if isinstance(inert, list) else []
        if ops:
            out.append(
                {
                    "kind": KIND_INERT,
                    "severity": SEVERITY_MEDIUM,
                    "screen_id": str(screen_id or ""),
                    "evidence_refs": ops,
                    "reproduction_prefix": reproduction,
                }
            )
        return out[:MAX_FINDINGS]
    except Exception:  # pragma: no cover - defensive; a detector must not fail a run
        logger.exception("mobile.oracles.findings failed")
        return out[:MAX_FINDINGS]
