"""Stage B: the reflection envelope the tester's chat model answers (no server model).

``build_packet`` returns an envelope for a run with failed or corrected steps, else
``{}``. ``apply_answer`` validates the model's answer: every text passes the secret
scan (one secret refuses the whole answer), invalid ops are dropped and counted,
ADD makes a lessons candidate (trust host), EDIT supersedes a lessons row, UPVOTE and
DOWNVOTE move votes and the evidence counters. Resubmission is a no-op and the gate
re-runs afterwards. Nothing here raises; every echo of stored text is wrapped.
"""

from __future__ import annotations

import json  # noqa: F401
import logging
import sqlite3  # noqa: F401
from typing import Any

from tools.mobile import (
    app_knowledge,  # noqa: F401
    knowledge_limits,  # noqa: F401
    knowledge_stage_gate,  # noqa: F401
    knowledge_trace,
)
from tools.mobile import knowledge_db as kdb  # noqa: F401
from tools.mobile.knowledge_reflect_answer import _reply  # noqa: F401
from tools.mobile.knowledge_reflect_answer import answer as _answer
from tools.mobile.knowledge_reflect_ops import (  # noqa: F401
    _ERRORS,
    _apply,
    _clean_ops,
    _one_op,
    _vote,
)
from tools.mobile.knowledge_reflect_packet import (  # noqa: F401
    _INSTRUCTION,
    _NOTICE,
    OPS,
    Qualified,
    _lessons_for,
    _qualifying,
    _read_conn,
    _step_view,
    assemble,
    schema,
)
from tools.untrusted import single_line, wrap_untrusted  # noqa: F401

logger = logging.getLogger(__name__)


def build_packet(package: str, run_id: str, steps: list | None = None) -> dict:
    """The reflection envelope for *run_id*, or ``{}`` when the run does not qualify."""
    try:
        if steps is None:
            steps = knowledge_trace.load_steps(run_id)
        bad = _qualifying(steps)
        if not bad:
            return {}
        return assemble(package, run_id, Qualified(bad, len(steps)))
    except _ERRORS:
        logger.exception("knowledge reflect build failed")
        return {}


def apply_answer(package: str, run_id: str, answer: Any) -> dict:
    """Apply the tester model's answer for *run_id*; never raises."""
    return _answer(package, run_id, answer)
