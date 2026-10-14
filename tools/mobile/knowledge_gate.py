"""Stage C: the ONLY place a learned candidate is promoted or demoted (contract W2d).

``gate(conn, run_id)`` applies, in order:

1. feedback of this run: a TESTER note is flagged ``disputed`` (never changed); a
   learned item gets ``contradicted`` counters (a context-bound observation makes a
   variant instead);
2. promotion: tester trust is active at once; a candidate becomes active at its
   table's distinct-run threshold;
3. contradiction beats confirmation: a learned item with ``contradicted`` over
   ``confirmed`` (and the floor) is superseded by the proposal, or goes stale;
4. retention decay of lessons; shortcuts that failed too often go stale.

Every write goes through ``knowledge_db`` with the run id (rollback restores it).
The decay and feedback steps are guarded by ``learn_marker`` events, so running the
gate again for one run (after a reflection) changes nothing twice. No model call.
Never raises: a failure returns what was done so far.
"""

from __future__ import annotations

import json  # noqa: F401
import logging
import sqlite3  # noqa: F401
from dataclasses import dataclass  # noqa: F401
from typing import Any

from tools.mobile import knowledge_db as kdb  # noqa: F401
from tools.mobile import (
    knowledge_feedback,  # noqa: F401
    knowledge_trace,
)
from tools.mobile import knowledge_limits as limits  # noqa: F401
from tools.mobile.knowledge_gate_core import (  # noqa: F401
    ERRORS as _ERRORS,
)
from tools.mobile.knowledge_gate_core import (  # noqa: F401
    KEEP_OUT as _KEEP_OUT,
)
from tools.mobile.knowledge_gate_core import (  # noqa: F401
    MIN_RUNS,
    TEXT_COLUMN,
    Case,
    Transition,
    Variant,
    runs_of,
)
from tools.mobile.knowledge_gate_core import is_open as _open  # noqa: F401
from tools.mobile.knowledge_gate_core import once as _once
from tools.mobile.knowledge_gate_core import row_of as _row  # noqa: F401
from tools.mobile.knowledge_gate_decay import (  # noqa: F401
    _decay,
    _failing_shortcuts,
)
from tools.mobile.knowledge_gate_feedback import (  # noqa: F401
    _count_contradiction,
    _dispute_tester_note,
    _feedback,
    _insert_variant,
    _variant_values,
)
from tools.mobile.knowledge_gate_promote import (  # noqa: F401
    _promote,
    _resolve_contradicted,
)

logger = logging.getLogger(__name__)


def gate(conn: Any, run_id: str, steps: list | None = None) -> list:
    """Run the gate for one run; the transitions made. Never raises."""
    out: list = []
    try:
        if steps is None:
            steps = knowledge_trace.load_steps(run_id)
        if _once(conn, run_id, "gate_feedback"):
            out += _feedback(conn, run_id)
        for table in MIN_RUNS:
            out += _promote(conn, run_id, table)
        for table in MIN_RUNS:
            out += _resolve_contradicted(conn, run_id, table)
        out += _failing_shortcuts(conn, run_id)
        if steps and _once(conn, run_id, "gate_decay"):
            out += _decay(conn, run_id, {str(s.get("screen_key") or "") for s in steps})
    except _ERRORS:
        logger.exception("knowledge gate failed for %s", str(run_id)[:64])
    return out
