"""Host mode: atomic-checklist job, extraction and section.

Facade: the code lives in agents/host_checklist_*.py; every name that callers or
tests import or patch here is re-exported.
"""

from __future__ import annotations

import logging

from agents.host_checklist_extract import extract_host_checklist  # noqa: F401
from agents.host_checklist_job import (  # noqa: F401
    _CHECKLIST_JOB_INSTRUCTIONS,
    _CHECKLIST_JOB_MARKER,
    CHECKLIST_JOB,
)
from agents.host_checklist_result import (  # noqa: F401
    _CL_MAX_TEXT_CHARS,
    HostChecklistResult,
    _bounded_checklist_entries,
)
from agents.host_checklist_section import build_host_checklist_section  # noqa: F401

logger = logging.getLogger("agents.host_mode")
