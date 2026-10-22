"""The ``update_trust`` row of the machine report (B22).

Two states: refusing unsigned releases (the default) and not requiring a
signature (the emergency escape hatch). A present signature is always verified.
"""

from __future__ import annotations

from tools import settings_port
from tools.machine_report_core import Row

UNSIGNED = "signature NOT required (QA_UPDATE_REQUIRE_SIGNATURE is off)"
REFUSING = "signed releases only (QA_UPDATE_REQUIRE_SIGNATURE is on)"


def update_trust_row() -> Row:
    """Whether updates are signed: policy only, never changes the gate."""
    if settings_port.get().qa_update_require_signature:
        return Row("update_trust", "ok", REFUSING)
    return Row("update_trust", "ok", UNSIGNED)
