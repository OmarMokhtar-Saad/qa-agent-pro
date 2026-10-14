"""tools/host_llm.py -- generic host ("boomerang") broker for server-side LLM calls.

``agents/host_*.py`` boomerangs test-case generation by SPLITTING one logical
operation into two stateless MCP tool calls (``qa_prepare_test_cases`` -> the host
model generates in its own chat turn -> ``qa_submit_suite``), with state persisted
in ``tools/prep_store.py``. That split is not a style choice: while a tool call is
executing the host is blocked waiting for its result, so no server-side code can
synchronously obtain a completion from the host mid-call. This module generalises
the split for the STANDALONE operations that are not part of a generation prep
(bug report, exploratory-coach step, standalone feature analysis, batched case
translation).

PHASE NOTE (superseded 2026-08-02 by Phase 4). Phase 1 shipped a default
submit-tool name, ``qa_submit_host_task``, expecting Phase 4 to register one
generic submit tool. Phase 4 did not register it, because it is REDUNDANT -- not
because it is impossible. Such a dispatcher COULD be built: ``close_task``
already reads ``kind`` from the SERVER-HELD task record (never from the submitted
payload), so dispatching on it would weaken no trust boundary. There is simply no
caller for it. Every mechanism-B caller already ships its OWN dedicated,
correctly-named submit tool with kind-specific submit-side logic --
``qa_submit_bug_report`` (section markers + RAG corpus seeding),
``qa_submit_explore_step`` (the ``<meta>`` line + coach memory),
``qa_submit_feature_analysis`` (``FeatureAnalysisReport`` coercion + rendering) --
and one shared tool would only concentrate those three unrelated side-effect sets
behind a single name for no actual user benefit. ``submit_tool`` therefore has NO
default tool name: a caller NAMES its submit tool, and an envelope built without
one honestly points the host at the tool named in the reply that handed it over,
instead of at a tool that was never registered.

Two mechanisms live here:

* ``open_task`` / ``build_envelope`` / ``close_task`` -- the prepare/submit split.
  The server keeps doing what only it can do (hardened fetching, ``_GUARD``
  wrapping, validation, persistence, exports); the host model does the writing.
  Untrusted handling is ENFORCED, not promised: ``build_envelope`` runs the user
  context through ``tools.untrusted.wrap_untrusted`` UNCONDITIONALLY -- it never
  decides by inspecting the content, because content that can contain the literal
  ``<untrusted_content`` could otherwise talk its way out of being wrapped -- and ``close_task``
  returns the parsed payload pre-tagged as model-derived/untrusted with a
  per-field character cap, tolerant fenced-or-prose JSON extraction, no ``eval``,
  a hard whole-submission size cap, and a one-shot ``task_id`` deleted on close.

It also owns the migration's DISCLOSURE surface (``UNMIGRATED_PATHS``,
``disclosure``, ``warn_once_if_degraded``): with ``QA_SERVER_LLM_ENABLED=false``
and ledger rows still unmigrated, those features are OFF, and an operator learns
that from ``qa-doctor`` and a one-time startup WARNING -- never from
behaviour. Phases 2-6 shrink ``UNMIGRATED_PATHS`` as each row migrates.

Note for readers coming from the plan brief: MCP *elicitation* (``ctx.elicit``,
used by the wizard pickers in ``handlers/``) asks the *human* for
primitive structured input. It cannot ask the client's *model* to generate and is
not an LLM substitute.

Contract (mirrors the rest of ``tools/``): every public coroutine returns
  {"error": None, "content": ...}   on success
  {"error": <str>, "content": None} on failure
and NEVER raises.
"""

from __future__ import annotations

import logging

from tools import prep_store  # noqa: F401
from tools.host_llm_allow import (  # noqa: F401
    allowed_and_unmigrated,
    allowed_paths,
    pending_paths,
    server_llm_retired,
    unknown_allow_ids,
    wildcard_allowed,
)
from tools.host_llm_consts import (  # noqa: F401
    _CREATED_BY,
    _KNOWN_KINDS,
    _MARKER,
    _MAX_CONTEXT_CHARS,
    _MAX_DEPTH,
    _MAX_FIELD_CHARS,
    _MAX_ITEMS,
    _MAX_RAW_CHARS,
    _SCHEMA_VERSION,
)
from tools.host_llm_disclosure import disclosure, disclosure_state  # noqa: F401
from tools.host_llm_envelope import _envelope_instructions, build_envelope  # noqa: F401
from tools.host_llm_ledger import LEDGER_IDS  # noqa: F401
from tools.host_llm_notes import (  # noqa: F401
    _bypassed_note,
    _complete_note,
    _off_note,
    _unknown_ids_note,
)
from tools.host_llm_parse import _cap, _extract_json, _loads  # noqa: F401
from tools.host_llm_request import HostTaskRequest  # noqa: F401
from tools.host_llm_task_close import (  # noqa: F401
    _claim,
    _closed_content,
    _load_open_task,
    _parsed_payload,
    _wrong_kind,
    close_task,
)
from tools.host_llm_task_open import _persist_task, _TaskSpec, open_task  # noqa: F401
from tools.host_llm_unmigrated import UNMIGRATED_PATHS  # noqa: F401
from tools.host_llm_warn import warn_once_if_degraded  # noqa: F401
from tools.host_llm_wrap import (  # noqa: F401
    _log_empty_wrap,
    _wrap_context,
    is_host_task_record,
)
from tools.safe_parse import loads_json  # noqa: F401
from tools.untrusted import wrap_untrusted  # noqa: F401

logger = logging.getLogger(__name__)


# A frozen ``HostTask`` record was drafted for this module and deliberately NOT
# shipped in Phase 1: nothing here instantiates one. ``open_task`` returns the
# ``{"error", "content": {"task_id", "envelope"}}`` dict that every ``tools/``
# function returns, so a dataclass would be a second, unused representation of the
# same task -- dead code in the foundation, and an invitation for the two shapes to
# drift. Phase 4 shipped the last of the real callers and none of them wanted one,
# so the dict stays the single representation; a typed handle arrives only with a
# user, never ahead of one.
