"""Hand an a11y record from the pre-run owner to the run that follows it."""

from __future__ import annotations

import logging

from tools.mobile.a11y_arming import handed_to
from tools.mobile.a11y_enable import _RUNS
from tools.mobile.a11y_record import clear_record, read_record, write_record

logger = logging.getLogger("tools.mobile.a11y")


def adopt(owner: str, run_id: str) -> bool:
    """Move the record *owner* wrote (the fast reader was turned on before the run
    existed) to *run_id*, so the run's own restore gives the settings back.
    ``False`` when there was nothing to move or the new record was not written."""
    if not owner or not run_id or owner == run_id:
        return False
    record = read_record(owner)
    if not record:
        return False
    if write_record(run_id, record).get("error"):
        logger.warning("mobile.a11y: record of %s not handed to %s", owner, run_id)
        return False
    clear_record(owner)
    handed_to(owner, run_id)
    serial = _RUNS.pop(str(owner), "") or str(record.get("serial") or "")
    if serial:
        _RUNS[str(run_id)] = serial
    return True
