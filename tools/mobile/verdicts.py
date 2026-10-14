"""Leaf: the case verdict vocabulary (``VERDICT_*``).

Cut from ``tools.mobile.case_runner`` so ``session_records`` can read the words
without importing an orchestrator. Imports nothing. ``case_runner`` re-exports
every name here (identity).
"""

from __future__ import annotations

VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_BLOCKED = "blocked"
#: Ran, but proved nothing: no assert passed and no action moved the screen.
#: Deliberately NOT folded into `blocked`, which means the case could not be
#: attempted -- this one WAS attempted and produced no evidence, and a tester
#: reading "blocked" would go looking for an obstacle that does not exist.
VERDICT_UNVERIFIED = "unverified"
