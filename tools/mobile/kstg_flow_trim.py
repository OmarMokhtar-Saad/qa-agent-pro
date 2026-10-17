"""Trimming a learned route before it is gated (see ``knowledge_stage_flows``).

Two parts of a clean run do not belong in a replayable flow:

* the PREAMBLE: steps taken before the package under test first shows in the
  foreground (an installer or launcher screen). Cut only when a later step is stamped
  with the package, so a run with no package information is left whole.
* a DECLINE ("Not Allow", "Deny") on a screen that is not the app's own (an OS
  permission dialog): replay answers those with the allow choice, so the step is
  dropped instead of refusing the whole route. A decline on the app's own sheet is
  part of its route and is kept; ``kstg_flow_gate`` accepts it. Never keeps a route shorter than
  ``MIN_STEPS``. Pure; never raises.
"""

from __future__ import annotations

from tools.mobile.kstg_flow_build import MIN_STEPS
from tools.mobile.kstg_flow_gate import foreign_decline
from tools.mobile.kstg_foreign import observed


def _first_in_app(recs: list, package: str) -> int:
    """Index of the first step stamped with *package*; 0 when none is."""
    return next((i for i, r in enumerate(recs) if observed(r) == package), 0)


def trim(recs: list, package: str) -> list:
    """*recs* without the pre-app preamble and the permission-decline taps."""
    body = recs[_first_in_app(recs, package) :] if package else recs
    kept = [r for r in body if not foreign_decline(r, package)]
    return kept if len(kept) >= MIN_STEPS else recs
