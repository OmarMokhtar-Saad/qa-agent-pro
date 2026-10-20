"""The ``update_trust`` row of the machine report (B22).

Three states are meant: unsigned by design, refusing, and signed. No release is
signed today (2026-10-06 decision), so the ``signed`` row is not built: nothing
could reach it.
"""

from __future__ import annotations

from tools import settings_port
from tools.machine_report_core import Row

UNSIGNED = "unsigned by design (releases carry no MANIFEST.sig)"
REFUSING = "refusing unsigned releases (QA_UPDATE_REQUIRE_SIGNATURE is on)"


def update_trust_row() -> Row:
    """Whether updates are signed: policy only, never changes the gate."""
    if settings_port.get().qa_update_require_signature:
        return Row("update_trust", "ok", REFUSING)
    return Row("update_trust", "ok", UNSIGNED)
