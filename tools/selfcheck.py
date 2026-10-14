"""Call every registered handler and read the reply this install really sends.

Tester-facing text goes stale silently. A settings field is deleted, a module
is deleted, a capability is hardcoded off -- and a live reply keeps naming it,
telling a tester to set a flag that no longer exists or to fix a backend
nothing in this tree can call. Two such strings were printed on every host
submit for weeks after the fields they named were deleted (CLAUDE.md, P2-I),
and both were found by READING handler output, not by review.

``tests/test_no_deleted_flag_in_output.py`` asks the same question statically,
over ``ast`` string constants, and that is the cheaper question -- it costs
nothing and cannot be fooled by a code path no test exercises. This module asks
the question the static scan structurally cannot:

* the static scan skips a whole string as soon as it matches its
  ``_RECORDS_DELETION`` pattern (``hardcoded``, ``RETIRED``, ``was deleted``,
  ...). That exemption is deliberate and load-bearing there, but it means a
  live string that BOTH records one deletion AND gives actionable advice naming
  a second, dead flag is invisible. Nothing here is exempt: a reply is judged
  as sent;
* no static scan can know which tools are REGISTERED in this edition, so it
  cannot see a DIRECTIVE naming a tool the tester's build never registered --
  the v1.77.0 defect class, reaching a tester at runtime instead of at build
  time;
* it scans every string in the tree, reachable or not, and therefore needs an
  allow-list of legitimate mentions. This scans only text a handler actually
  returned, so a mention here is by construction tester-reachable.

**Two callers, one module** (see docs/TOOLS.md): ``qa_selfcheck`` for a tester,
who cannot run pytest, and ``tests/test_selfcheck.py`` for CI, which cannot
depend on an MCP client. The assertions live here so neither caller can hold a
different opinion about what "clean" means.

**It imports nothing internal except its sibling ``tools.capabilities``**, for
the reason recorded there: the Constitution forbids a ``tools/`` module from
importing ``mcp_server`` or ``agents/``, so the live server object, the
``Settings`` CLASS, the ``llm`` module and the install root are all INJECTED by
the caller. That is also what makes every check below provable with a fake
server rather than a booted one.

Safety, stated rather than assumed -- **no invocation here can perform an
external write**, and four independent things enforce it, three structurally:

1. :func:`build_payload` derives every argument from the tool's own JSON schema
   and emits a parameter's DECLARED DEFAULT or a typed fixture seed. It never
   emits a non-default boolean, so ``apply`` / ``*_ack`` / ``proceed_anyway``
   are always the ``False`` they ship as -- and ``apply=true`` is required for
   every external write in this tree (``qa_push_suite``, ``qa_write_api_test``,
   every mobile install/launch);
2. :data:`SEED` is deliberately not a valid enum value for any parameter, so
   ``qa_push_suite`` is refused by name at its unknown-target check, BEFORE a
   credential is read. ``tests/test_selfcheck.py`` pins that the seed matches
   none of the patterns below and is not a push target;
3. :func:`egress_guard` blocks and RECORDS every non-loopback socket connection
   attempted for the duration of the pass. Loopback stays open because ``adb``
   talks to 127.0.0.1:5037 and device listing is a real reply surface;
4. the runtime caller enumerates the LIVE server and never flips a setting, so
   a kill-switch that is OFF stays OFF and its tools are not even registered.

What it does have side effects on, disclosed because a silent one is worse: a
pass mints a few one-shot ``task_id``s (bug report, coach, feature analysis)
that expire with the prep TTL and are never submitted, writes the ordinary
audit rows for the handlers it calls, and shells out to ``adb`` for device
listing where ``adb`` exists.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import inspect  # noqa: F401
import logging  # noqa: F401
import re  # noqa: F401
import socket  # noqa: F401
from contextlib import contextmanager  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Callable, Iterator, Mapping, cast  # noqa: F401

from tools import capabilities  # noqa: F401
from tools.selfcheck_collect import (  # noqa: F401
    _accepts_ctx,
    _as_text,
    _collect_prompt,
    _collect_tool,
    _maybe_await,
    collect,
    logger,
)
from tools.selfcheck_consts import (  # noqa: F401
    FLOOR_CHARS_PER_SURFACE,
    FLOOR_FLAG_TOKENS,
    FLOOR_TOOL_MENTION_DIVISOR,
    FLOOR_TOOL_MENTIONS_MIN,
    INVOKE_TIMEOUT_S,
    KINDS,
    SCHEMA,
    SEED,
    SELF_TOOL_NAME,
)
from tools.selfcheck_egress import egress_guard  # noqa: F401
from tools.selfcheck_floor import (  # noqa: F401
    floor_violations,
    unaccounted,
)
from tools.selfcheck_live import (  # noqa: F401
    ENV_RESIDUAL,
    LIVE_ENV,
    classify_live_env,
    live_names,
)
from tools.selfcheck_patterns import (  # noqa: F401
    _LLM_SYMBOL,
    _LOOPBACK,
    _MODEL,
    _MODULE,
    _SERVER_MODEL_CLAIM,
    _TOKEN,
    _TOOL,
)
from tools.selfcheck_payload import (  # noqa: F401
    build_payload,
    non_default_booleans,
)
from tools.selfcheck_render import (  # noqa: F401
    _render_egress,
    _render_findings,
    _verdict_line,
    render,
)
from tools.selfcheck_run import (  # noqa: F401
    _LOCK,
    run,
)
from tools.selfcheck_scan import (  # noqa: F401
    _module_exists,
    _scan_flags,
    _scan_llm_symbols,
    _scan_model_claims,
    _scan_modules,
    _scan_surface,
    _scan_tools,
    _ScanTally,
    scan,
)
from tools.selfcheck_types import (  # noqa: F401
    Collected,
    CollectOpts,
    EgressBlocked,
    Finding,
    FixtureContext,
    RunReq,
    ScanCtx,
    Skip,
)
