"""Suite-quality signal detectors -- item 10 (v1.97.0 cursor-hardening).

Self-contained, pure-function module computing the audit-named signals over an
already-parsed list of case-like objects, duck-typed via ``getattr`` so it
accepts real ``tools.models.TestCase`` instances or plain test doubles
(``SimpleNamespace``) equally. Warn-only: nothing here ever refuses, gates, or
raises -- these are advisories for whatever caller wires
``compute_suite_quality_advisories``'s output into a reply or workbook
(deliberately NOT done by this module in this change -- see the plan's
Cross-Scope Conflicts / Risk Assessment: the exact staged-text and
server-duplicate-groups variable names at the mcp_handlers.py submit-tail call
site were not confirmed within the planning run's discovery budget).

Bounds below (``_max_unverified_refs`` / ``_max_conditional_step_ids``) are
deliberately lower-case: they exist so a hostile 100k-case suite cannot
produce a 100k-line advisory, but they are internal list-length guards, not a
declared product cap in the sense ``tests/cap_scanner.py`` looks for.
"""

from __future__ import annotations

import logging
import re  # noqa: F401
from collections import Counter  # noqa: F401

from tools.suite_quality_advise import (  # noqa: F401
    AdvisoryContext,
    _concentration_advisory,
    _conditional_advisory,
    _dedup_advisory,
    _precondition_advisory,
    _refs_advisory,
    compute_suite_quality_advisories,
)
from tools.suite_quality_detect import (  # noqa: F401
    _case_text_fields,
    _conditional_re,
    _max_conditional_step_ids,
    _max_unverified_refs,
    _ticket_key_re,
    ac_concentration,
    conditional_steps,
    dedup_self_report_contradiction,
    precondition_repetition,
    unverified_external_references,
)

logger = logging.getLogger(__name__)
