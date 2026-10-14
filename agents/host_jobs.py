"""Host mode: HostJob, job attachment and the ambiguity job/result.

Facade: the code lives in agents/host_jobs_*.py; every name that callers or tests
import or patch here is re-exported. ``attach_jobs`` is defined here.
"""

from __future__ import annotations

import logging

from agents.host_jobs_attach import _index_jobs, _stamp_jobs
from agents.host_jobs_core import (  # noqa: F401
    _AMBIGUITY_JOB_INSTRUCTIONS,
    _AMBIGUITY_RETURN_CLAUSE,
    _AMBIGUITY_RETURN_MARKER,
    _COLLISION_CLAUSE,
    HostJob,
)
from agents.host_jobs_result import (  # noqa: F401
    HostAmbiguityResult,
    extract_ambiguity_result,
)
from agents.host_jobs_section import (  # noqa: F401
    ambiguity_notes_gen_note,
    build_ambiguity_result_section,
)
from agents.host_jobs_spec import attach_ambiguity_job  # noqa: F401
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


def attach_jobs(payload: dict, jobs=()) -> dict:
    """Attach HostJobs to a prepare payload + build the `jobs_to_run` index.

    A NO-OP when there is nothing to attach and no legacy job key is present:
    the returned payload is then key-identical to the input, so a flag-OFF
    prepare is byte-identical to the pre-feature output. Never raises.
    """
    out = dict(payload or {})
    with never_raise(logger, "attach_jobs failed", exc_info=True):
        index, legacy = _index_jobs(out, jobs)
        if not index:
            return dict(payload or {})
        _stamp_jobs(out, index, legacy)
        return out
    return dict(payload or {})
