"""Host mode: acceptance-criteria job, extraction and section (facade).

The implementation lives in agents/host_ac_*.py; every name callers or tests reach
through here is re-exported from its home. Cut verbatim from agents/host_mode.py (P4.3).
"""

from __future__ import annotations

from agents.host_ac_extract import extract_host_acs  # noqa: F401
from agents.host_ac_job import (  # noqa: F401
    _AC_JOB_INSTRUCTIONS,
    _AC_JOB_MARKER,
    _AC_JOB_SPEC,
    AC_JOB,
)
from agents.host_ac_result import _AC_MAX_ITEMS, HostACResult  # noqa: F401
from agents.host_ac_section import build_host_ac_section  # noqa: F401
