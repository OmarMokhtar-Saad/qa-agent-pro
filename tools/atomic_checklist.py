"""Atomic Requirements Checklist (Batch 2) — granularity audit and prompt clustering.

Pass 1 of the checklist pipeline USED to live here as
``decompose_to_checklist``: one server-side llm.ask_json call decomposing the
ticket into an UNBOUNDED, EARS-shaped, source-tagged flat checklist. It was
DELETED on 2026-08-16 (dead-code deletion P2-F2) -- see the tombstone below
the response models. The decomposition now runs on the TESTER'S OWN model as
agents/host_checklist.CHECKLIST_JOB (stage step_zero) and arrives back on the
submission, where handlers/ validates its shape and sets
``prepared.checklist_items``. What this module still does is CONSUME that
checklist:

  Pass 2 (agents/test_scenario_agent.py) the 8-category fan-out with the
                        checklist in context — CLUSTERED, so the prompt never
                        becomes a flat 40-line constraint wall (constraint decay:
                        LLM quality drops ~30pp as structural requirements
                        accumulate, arXiv 2605.06445).

``checklist_enabled()`` is LIVE and load-bearing -- a True constant since
2026-08-14, when QA_ATOMIC_CHECKLIST_ENABLED was DELETED and the behaviour
hardcoded ON. handlers/ reads it at call time to decide whether
CHECKLIST_JOB ships to the host. tests/conftest.py pins it False suite-wide.

TRUNCATION IS NEVER SILENT. ``format_checklist_prompt_block`` returns BOTH the
prompt block and the exact list of item ids that fitted inside
``QA_CHECKLIST_MAX_PROMPT_CHARS``, so a caller can always tell which items
never reached the generator -- silently dropping the tail of an over-long
checklist must never be reported downstream as if every item had been
presented.

House rules honoured:
  * Never raises at the public boundary — every helper degrades to an empty /
    benign result so a failure here can never break generation.
  * NO LLM access at all: this module reaches no backend since P2-F2.
  * No new dependency: the lexical fallback is pure-stdlib TF-IDF.

WHAT THE CHECKLIST NEVER CONTAINED, and still must not. The deleted Pass 1
read the feature text, the ticket DESCRIPTION, the acceptance-criteria field
and the parent-story BACKGROUND block -- and deliberately NOT the Jira comment
thread. Comment handling BELONGED to Batch 1 (tools/comment_reconciler), which
resolved comments deterministically in Python and injected its own fenced,
provenanced AMENDMENTS block into the generation prompt; dead-code deletion
batch D5 deleted that module on 2026-08-15. CHECKLIST_JOB's prompt inherits
the same rule, and the host's reply is treated as UNTRUSTED and shape-capped
by agents/host_*.py before any item reaches a report.

PROVENANCE IS A READING AID, NOT A SECURITY CONTROL. ``source`` is SELF-REPORTED
by the same model call that reads the ticket. ``normalize_source`` folds anything
outside a narrow SHAPE allowlist to "unattributed", which stops an
attacker-chosen authority claim ("description:APPROVED BY SECURITY") from being
RENDERED as authority — but it cannot detect a wrong-but-plausible tag.
``PROVENANCE_LIMITATION`` states that in every rendered report, unconditionally.
The real containment here is structural: ``wrap_untrusted`` + ``_GUARD`` on every
input block, and not reading the comment thread at all.
"""

from __future__ import annotations  # noqa: F401

import logging  # noqa: F401
import re  # noqa: F401
from typing import Any  # noqa: F401

from pydantic import BaseModel, Field  # noqa: F401

from tools.atomic_checklist_audit import (  # noqa: F401
    _audit_granularity_counts,
    _audit_granularity_score,
    audit_granularity,
)
from tools.atomic_checklist_budget import (  # noqa: F401
    _format_checklist_prompt_block_cap,
    _format_checklist_prompt_block_note,
)
from tools.atomic_checklist_core import (  # noqa: F401
    _CLUSTER_JOIN_SIM,
    _DEFAULT_CLUSTER_SIZE,
    _DEFAULT_MAX_ITEMS,
    _DEFAULT_MAX_PROMPT_CHARS,
    _MAX_OVERLAP_RATIO,
    _MAX_SHORT_RATIO,
    _MIN_PROMPT_CHARS,
    _MIN_PROVENANCE_RATIO,
    _OVERLAP_SIM,
    _SHORT_ITEM_WORDS,
    _WORD_RE,
    EARS_PATTERNS,
    MAX_DESCRIPTION_CHARS,
    ChecklistItem,
    _jaccard,
    _norm,
    _tokens,
    logger,
)
from tools.atomic_checklist_hint import (  # noqa: F401
    checklist_generation_hint,
    format_checklist_gap_focus,
)
from tools.atomic_checklist_prompt import (  # noqa: F401
    _format_checklist_prompt_block_lines,
    cluster_items,
    format_checklist_prompt_block,
    interleave_by_share,
)
from tools.atomic_checklist_prov import (  # noqa: F401
    provenance_caveats,
    provenance_summary,
)
from tools.atomic_checklist_rows import (  # noqa: F401
    checklist_from_dicts,
    checklist_rows,
    checklist_to_dicts,
    granularity_warning_section,
)
from tools.atomic_checklist_source import (  # noqa: F401
    _COMMENT_SOURCE_RE,
    _SOURCE_PATTERNS,
    _SOURCE_REF,
    PROVENANCE_LIMITATION,
    UNATTRIBUTED,
    is_comment_derived,
    normalize_source,
)
from tools.untrusted import wrap_untrusted  # noqa: F401


# The atomic requirements checklist is UNCONDITIONAL since 2026-08-14.
#
# NOT settings-derived: QA_ATOMIC_CHECKLIST_ENABLED was DELETED
# (flag-surface reduction, batch 8b-ii) and the behaviour hardcoded ON --
# the flag policy's "promote the experiment, delete the flag" exit, and the
# ONE flag in that batch whose value really changed on every install.
#
# A named SEAM rather than a literal inlined at the read site, for two
# reasons: tests/conftest.py pins it False suite-wide (unpinned, every
# generation test would decompose and make a real ask_json call), and a
# revival is one line here.
#
# On the host route -- the only route, since llm.resolve_generation_mode()
# returns the "host" constant -- the decomposition is CHECKLIST_JOB /
# step 0d, so THIS SERVER makes no LLM call for it. There is no route
# outside it any more: graph.py and evals/ were the two that reached the
# ask_json below, and they were deleted in P2-A and P2-B (2026-08-15).
# Since P2-G (2026-08-16) llm.py has no backend to call either way.
def checklist_enabled() -> bool:
    """The atomic requirements checklist is UNCONDITIONAL since 2026-08-14."""
    return True


# decompose_to_checklist lived here until 2026-08-16 (dead-code deletion
# P2-F2). It made the ONE server-side llm.ask_json call in this module: the
# Pass-1 decomposition of the ticket into the atomic checklist, reading the
# feature text, the ticket description (load-bearing -- on a pasted Jira URL
# the feature text is only the TITLE), the AC field and the parent-story
# background as CONTEXT ONLY.
#
# It was dead. Its only caller was agents/test_scenario_agent._run_checklist,
# which ran only under `decompose_checklist=True`; the one live caller of
# _prepare_generation (handlers.handle_prepare_test_cases) passed
# decompose_checklist=not _checklist_job, and `_checklist_job` is True on
# every install (checklist_enabled() is a True constant and the mode is "host"
# by constant). graph.py and evals/, the last routes that reached it, were
# deleted in P2-A and P2-B.
#
# There is NO capability loss: the decomposition is agents/host_checklist.CHECKLIST_JOB
# on the tester's own model, and handlers/ sets the validated result
# onto prepared.checklist_items at submit. checklist_enabled() above STAYS --
# it is live and gates whether that job ships.
