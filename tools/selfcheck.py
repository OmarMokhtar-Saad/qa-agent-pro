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

import asyncio
import inspect
import logging
import re
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from tools import capabilities

logger = logging.getLogger(__name__)

#: Bumped when the report shape changes incompatibly.
SCHEMA = 1

#: This tool's own registered name. It is skipped (and the skip is COUNTED),
#: because invoking it from inside itself recurses. A test asserts the
#: registered name equals this, so a rename cannot silently turn the skip into
#: a no-op that lets the recursion back in.
SELF_TOOL_NAME = "qa_selfcheck"

#: The one fixture value handed to a required parameter. Deliberately shaped so
#: it matches NONE of the patterns below (no ``qa_`` prefix, no ``.py``, no
#: SHOUTING_CASE) and is not a valid value for any enum-ish parameter in the
#: tree -- an echo of it must never be able to look like a finding, and a push
#: target must never be able to look valid. Measured: an earlier seed of
#: ``qa-selfcheck-probe`` produced three phantom findings by being echoed back.
SEED = "selfcheck-fixture"

#: Per-invocation ceiling. Measured 2026-09-04 over all 27 tools of a full
#: edition: the slowest was 4.3s (``adb`` device listing).
INVOKE_TIMEOUT_S = 45.0

#: Bounds that keep the absence assertions from being ambient-satisfiable. An
#: absence check passes trivially the moment its detector stops matching, so a
#: pass reports HOW MUCH it scanned and :func:`floor_violations` refuses to call
#: a thin pass clean.
#:
#: They are all EDITION-RELATIVE, and that is a correction of a measured
#: mistake: absolute floors calibrated on a full edition (24 surfaces) reported
#: the public test-cases-only build (15 surfaces, 12 tools) as inconclusive,
#: which would have made the tool useless for exactly the tester it is for.
#: Measured 2026-09-04: full edition 34 surfaces / 33,177 chars / 25 distinct
#: tool names / 3 flag tokens; dist edition 15 / 15,652 / 11 / 2.
#:
#: The primary guard is not a floor at all but the COMPLETENESS equality in
#: :func:`unaccounted`: every registered tool is either read or a counted skip.
#: A floor can be tuned; an equality cannot be quietly satisfied.
#:
#: There is deliberately NO bound on module or model tokens: today's replies
#: contain ZERO of either (measured, both editions), so any floor would be a
#: lie. Those two checks are proven ONLY by their positive controls in
#: ``tests/test_selfcheck.py``, and the report prints their scanned count so the
#: zero is visible rather than implied.
FLOOR_CHARS_PER_SURFACE = 300
FLOOR_TOOL_MENTION_DIVISOR = 3
FLOOR_TOOL_MENTIONS_MIN = 3
FLOOR_FLAG_TOKENS = 1

#: Env vars that are legitimately named in tester-facing text and are NOT
#: ``Settings`` fields. This is the canonical copy --
#: ``tests/test_no_deleted_flag_in_output.py`` imports it rather than keeping a
#: second list, because two copies of one allow-list is the rot site a
#: self-audit is most likely to grow.
#:
#: Every name here must be justified by :func:`classify_live_env`, which is what
#: stops the list becoming a place to silence a finding.
LIVE_ENV: frozenset = frozenset(
    {
        "QA_MCP_ENABLED",
        "QA_EXPORT_DIR",
        "QA_DIST_MODE",
        "QA_UPDATE_REPO",
        "QA_EMBEDDINGS_BACKEND",
        "QA_POSTHOG_KEY",
        "QA_FORCE",
        "QA_DRIFT_RESTART_ENABLED",
        "QA_DRIFT_CHECK_SECONDS",
        "QA_RELEASE_SIGNING_KEY",
        "QA_INSTALL_DIR",
        "QA_PY",
        "QA_UPDATE_INTERVAL_MINUTES",
        "QA_LOG_RETENTION_ENABLED",
        "QA_LOG_RETENTION_DAYS",
        "QA_LOG_RETENTION_MAX_FILES",
        "API_AUTH_TOKEN",
        "VOYAGE_API_KEY",
        "GITHUB_TOKEN",
    }
)

#: The residue: names in :data:`LIVE_ENV` that are neither a ``Settings`` field
#: nor read through ``os.environ`` in this tree, each with the reason it is
#: still legitimate. Count-pinned by the tests in BOTH directions, exactly like
#: the static scan's ``RETAINED`` table. This is the one circular corner of the
#: allow-list -- the string that names ``API_AUTH_TOKEN`` is what keeps
#: ``API_AUTH_TOKEN`` exempt -- so it is kept small, named and enumerated rather
#: than hidden inside the set above.
ENV_RESIDUAL: dict = {
    "API_AUTH_TOKEN": (
        "the TESTER exports this to run the generated Java suite; this server "
        "never receives, stores or writes it, and saying so is the point of "
        "the strings that name it"
    ),
    "QA_FORCE": "read by the installer/launcher shell templates, not by Python",
    "QA_INSTALL_DIR": "installer/launcher shell template variable",
    "QA_PY": "installer/launcher shell template variable",
}

#: Same two-armed shape as the static scan's ``_TOKEN``: prefix-bound for flag
#: names, suffix-bound for credential names under ANY prefix (the arm that was
#: missing when two ``*_API_KEY`` strings survived P2-I).
_TOKEN = re.compile(
    r"\b(?:(?:QA|JIRA|TESTRAIL|XRAY)_[A-Z0-9_]{2,}"
    r"|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_(?:API_KEY|TOKEN))\b"
)

#: A module a reply points the reader at. Deleted modules cannot be enumerated
#: (they are gone), so the check is INVERTED and needs no list: a reply that
#: names ``<something>.py`` must name a file that exists.
_MODULE = re.compile(r"\b((?:[a-z_][a-z0-9_]*/)*[a-z_][a-z0-9_]*\.py)\b")

#: ``llm.<symbol>`` -- checked against the module's real namespace. ``llm.py``
#: is 93 lines and exports four symbols; ``ask`` / ``ask_json`` / ``ask_vision``
#: were deleted with every backend on 2026-08-16.
_LLM_SYMBOL = re.compile(r"\bllm\.([a-z_][a-z0-9_]*)")

#: A tool name. Underscore-bound, plus the one registered hyphenated name. A
#: bare ``qa-[a-z]+`` arm matched the PRODUCT name ("qa-agents") and the fixture
#: seed when this was measured, which is the shape of a check reacting to a
#: string it sent itself.
_TOOL = re.compile(r"\bqa_[a-z0-9_]+\b|\bqa-doctor\b")

#: Nothing in this tree can call a model, so a tester-facing reply has no
#: legitimate reason to name one, nor to send the reader off to fix a "backend".
#: The ``claude-`` arm requires a model FAMILY or a separated version, never
#: bare digits: the first spelling was ``claude-[a-z0-9.-]{3,}``, which matched
#: ``claude-501`` out of the install path ``/tmp/claude-501/...`` that
#: ``qa-doctor`` prints. ``/tmp/claude-<uid>/`` is the standard Claude Code
#: scratchpad layout, so that spelling reported a false finding for any tester
#: whose checkout sat under one -- on the very tool a confused tester is told
#: to run first. Matches claude-opus-5, claude-sonnet-5,
#: claude-haiku-4-5-20251001, claude-fable-5-1, claude-3-5-sonnet; not
#: claude-501 and not claude-code.
_MODEL = re.compile(
    r"claude-(?:opus|sonnet|haiku|fable|instant|[0-9]+[.\-][0-9])[a-z0-9.\-]*"
    r"|gpt-[0-9o]|\bsonnet\b|\bopus\b|\bhaiku\b|"
    r"LLM backend|model backend",
    re.I,
)

#: Hosts the egress guard lets through. ``adb`` reaches its server on
#: 127.0.0.1:5037 and device listing is a reply surface worth scanning.
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", ""})

#: One pass at a time: the guard patches process-wide socket entry points, and
#: two overlapping passes would restore them out of order.
_LOCK = asyncio.Lock()

#: The finding kinds, in report order. Named here so a caller (and a test) can
#: assert on the SET rather than on strings scattered through the scanner.
KINDS: tuple = (
    "dead_flag",
    "dead_module",
    "absent_llm_symbol",
    "model_reference",
    "unregistered_tool",
    "invocation_error",
)


class EgressBlocked(RuntimeError):
    """A handler tried to open a non-loopback connection during a pass."""


@dataclass(frozen=True)
class Finding:
    """One stale or unreachable thing a real reply named."""

    kind: str
    surface: str
    detail: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "surface": self.surface, "detail": self.detail}


@dataclass(frozen=True)
class Skip:
    """A surface the pass could not read, and why.

    A skip is never silent: it is counted, listed in the report and rendered to
    the tester. A silently skipped handler is how a gate becomes inert.
    """

    surface: str
    reason: str

    def as_dict(self) -> dict:
        return {"surface": self.surface, "reason": self.reason}


# ------------------------------------------------------------- payloads ----- #


def build_payload(schema: Mapping | None) -> tuple:
    """``(kwargs, unseedable)`` for one tool, derived from its own JSON schema.

    There is NO per-handler fixture table, on purpose: a hand-written fixture
    per handler goes stale exactly like a hand-written tool list, and a stale
    fixture fails as a ``TypeError`` that reads like a bug in the handler. The
    schema is generated by fastmcp FROM the handler's signature, so it cannot
    drift from it.

    The rules are deliberately narrow:

    * a parameter with a declared default gets that default, whatever its type.
      This is the load-bearing one: every acknowledgement argument and every
      ``apply`` in this tree defaults to ``False``, so no invocation can ask for
      an external write;
    * a REQUIRED parameter gets a typed seed -- :data:`SEED` for a string, ``0``
      for a number, ``False`` for a boolean, ``[]`` for an array;
    * a required parameter of any other type is refused and named in
      ``unseedable``, which makes that surface a counted SKIP rather than an
      exception that reads like a defect.
    """
    props = dict((schema or {}).get("properties") or {})
    required = set((schema or {}).get("required") or ())
    kwargs: dict = {}
    unseedable: list = []
    for name, spec in props.items():
        spec = spec if isinstance(spec, Mapping) else {}
        if "default" in spec:
            kwargs[name] = spec["default"]
            continue
        if name not in required:
            continue
        kind = spec.get("type")
        if kind in (None, "string"):
            kwargs[name] = SEED
        elif kind in ("integer", "number"):
            kwargs[name] = 0
        elif kind == "boolean":
            kwargs[name] = False
        elif kind == "array":
            kwargs[name] = []
        else:
            unseedable.append(f"{name}:{kind}")
    return kwargs, sorted(unseedable)


def non_default_booleans(schema: Mapping | None, kwargs: Mapping) -> list:
    """Boolean arguments whose value is not the schema's default.

    Safety layer 1 as a function, so a test can assert it over every tool of
    every edition instead of trusting the prose in :func:`build_payload`.
    """
    props = dict((schema or {}).get("properties") or {})
    out: list = []
    for name, value in kwargs.items():
        if not isinstance(value, bool):
            continue
        spec = props.get(name)
        spec = spec if isinstance(spec, Mapping) else {}
        if value != bool(spec.get("default", False)):
            out.append(name)
    return sorted(out)


# --------------------------------------------------------------- egress ----- #


@contextmanager
def egress_guard(recorder: list, label: Callable | None = None) -> Iterator:
    """Block and record every non-loopback connection for the duration.

    Each attempt is recorded as ``(surface, host)``. The ATTRIBUTION is the
    point: "something tried to reach the network" is not a testable claim,
    while "the push tool tried to reach the network" is, and that is the
    assertion that keeps this pass from becoming a third route to the one
    credentialed outbound write in the tree.

    Restores both entry points in a ``finally``, so a raising handler cannot
    leave the process without networking.
    """
    where = label or (lambda: "")
    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def _host_of(address: Any) -> str:
        if isinstance(address, tuple) and address:
            return str(address[0])
        return str(address)

    def _blocked(host: str) -> bool:
        return bool(host) and host not in _LOOPBACK and not host.startswith("/")

    def connect(self, address, *args, **kwargs):
        host = _host_of(address)
        if _blocked(host):
            recorder.append((where(), host))
            raise EgressBlocked(f"self-check blocked an outbound connection to {host}")
        return real_connect(self, address, *args, **kwargs)

    def create_connection(address, *args, **kwargs):
        host = _host_of(address)
        if _blocked(host):
            recorder.append((where(), host))
            raise EgressBlocked(f"self-check blocked an outbound connection to {host}")
        return real_create(address, *args, **kwargs)

    socket.socket.connect = connect
    socket.create_connection = create_connection
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.create_connection = real_create


# -------------------------------------------------------------- fixture ----- #


class FixtureContext:
    """The smallest thing that looks like an MCP ``Context`` to a handler.

    ``elicit`` RAISES rather than answering: a self-check must never put a
    dialog in front of a tester, and the refusal makes the handler fall back to
    its markdown menu -- which is itself a tester-facing reply worth scanning.
    Everything else is an awaitable no-op.
    """

    client_name = "qa_selfcheck"

    def __init__(self) -> None:
        self.session = type(
            "_Session",
            (),
            {
                "client_params": type(
                    "_Params",
                    (),
                    {
                        "clientInfo": type(
                            "_Info", (), {"name": self.client_name, "version": "0"}
                        )()
                    },
                )()
            },
        )()

    async def elicit(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the self-check never elicits from a tester")

    def __getattr__(self, name: str) -> Callable:
        async def _noop(*args: Any, **kwargs: Any) -> None:
            return None

        return _noop


# ------------------------------------------------------------ collection ---- #


def _as_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, (list, tuple)):
        return "\n".join(str(getattr(block, "text", "") or "") for block in result)
    return str(result or "")


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _accepts_ctx(fn: Callable) -> bool:
    try:
        return "ctx" in inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover - C builtins only
        return False


@dataclass
class Collected:
    """Everything one pass observed, before any judgement is applied."""

    surfaces: dict
    skips: list
    findings: list
    egress: list
    registered: frozenset


async def collect(
    server: Any,
    *,
    ctx_factory: Callable | None = None,
    timeout: float = INVOKE_TIMEOUT_S,
    self_name: str = SELF_TOOL_NAME,
) -> Collected:
    """Invoke every registered tool and read every static tester-facing text."""
    factory = ctx_factory or FixtureContext
    tools = await capabilities.tools_by_name(server)
    prompts = await capabilities.prompts_by_name(server)
    surfaces: dict = {}
    skips: list = []
    findings: list = []
    egress: list = []

    instructions = getattr(server, "instructions", "") or ""
    if instructions:
        surfaces["instructions"] = instructions
    else:
        skips.append(Skip("instructions", "this server exposes no instructions block"))

    at: dict = {"surface": ""}
    with egress_guard(egress, lambda: at["surface"]):
        for name in sorted(tools):
            key = f"tool:{name}"
            at["surface"] = key
            if name == self_name:
                skips.append(
                    Skip(key, "the self-check itself; invoking it would recurse")
                )
                continue
            fn = getattr(tools[name], "fn", None)
            if not callable(fn):
                skips.append(Skip(key, "the registry entry exposes no callable"))
                continue
            schema = getattr(tools[name], "parameters", None)
            kwargs, unseedable = build_payload(schema)
            if unseedable:
                skips.append(
                    Skip(
                        key,
                        "no fixture value for required parameter(s): "
                        + ", ".join(unseedable),
                    )
                )
                continue
            if _accepts_ctx(fn):
                kwargs["ctx"] = factory()
            try:
                result = await asyncio.wait_for(_maybe_await(fn(**kwargs)), timeout)
            except Exception as exc:  # never raise to the caller
                logger.warning("self-check: %s raised", key, exc_info=True)
                findings.append(
                    Finding(
                        "invocation_error", key, f"{type(exc).__name__}: {exc}"[:300]
                    )
                )
                continue
            surfaces[key] = _as_text(result)

        for name in sorted(prompts):
            key = f"prompt:{name}"
            at["surface"] = key
            fn = getattr(prompts[name], "fn", None)
            if not callable(fn):
                skips.append(Skip(key, "the registry entry exposes no callable"))
                continue
            try:
                surfaces[key] = _as_text(await _maybe_await(fn()))
            except Exception as exc:  # never raise to the caller
                logger.warning("self-check: %s raised", key, exc_info=True)
                findings.append(
                    Finding(
                        "invocation_error", key, f"{type(exc).__name__}: {exc}"[:300]
                    )
                )

    return Collected(
        surfaces=surfaces,
        skips=skips,
        findings=findings,
        egress=egress,
        registered=frozenset(tools),
    )


# -------------------------------------------------------------- scanning ---- #


def live_names(settings_cls: Any) -> frozenset:
    """Every env name a reply may legitimately tell a tester to set."""
    fields = getattr(settings_cls, "model_fields", {}) or {}
    return frozenset({str(name).upper() for name in fields}) | LIVE_ENV


def classify_live_env(settings_cls: Any, source_text: str) -> dict:
    """``{name: "field" | "environ" | "residual" | "unjustified"}``.

    The anti-rot guard for :data:`LIVE_ENV` itself. ``source_text`` is the
    shipped Python source, supplied by the caller (this module reads no tree of
    its own): a name counts as ``environ`` when it appears within 120 characters
    of an environment read. Anything left must be listed in
    :data:`ENV_RESIDUAL` with a reason, or it is ``unjustified`` and the tests
    fail.
    """
    fields = {
        str(name).upper() for name in (getattr(settings_cls, "model_fields", {}) or {})
    }
    reads = "\n".join(
        re.findall(r"(?:environ|getenv|_setting|_env)[^\n]{0,120}", source_text)
    )
    out: dict = {}
    for name in sorted(LIVE_ENV):
        if name in fields:
            out[name] = "field"
        elif re.search(rf"\b{re.escape(name)}\b", reads):
            out[name] = "environ"
        elif name in ENV_RESIDUAL:
            out[name] = "residual"
        else:
            out[name] = "unjustified"
    return out


def _module_exists(root: Path, rel: str) -> bool:
    return any(
        (root / prefix / rel).exists() if prefix else (root / rel).exists()
        for prefix in ("", "tools", "agents")
    )


def scan(
    surfaces: Mapping,
    *,
    registered,
    live,
    llm_symbols,
    root: Path,
) -> tuple:
    """Judge collected text. Returns ``(findings, scanned)``.

    ``scanned`` is not decoration. Every check here asserts an ABSENCE, and an
    absence assertion passes for free the moment its detector stops matching --
    so each one reports how many candidates it actually saw, and the tests turn
    those counts into floors.
    """
    findings: list = []
    tool_mentions: set = set()
    flag_tokens: set = set()
    module_tokens: set = set()
    model_tokens: set = set()

    for surface in sorted(surfaces):
        text = surfaces[surface] or ""
        for token in sorted(set(_TOKEN.findall(text))):
            if token.endswith("_"):  # a prefix reference, never a field name
                continue
            flag_tokens.add(token)
            if token not in live:
                findings.append(Finding("dead_flag", surface, token))
        for rel in sorted(set(_MODULE.findall(text))):
            module_tokens.add(rel)
            if not _module_exists(root, rel):
                findings.append(Finding("dead_module", surface, rel))
        for symbol in sorted(set(_LLM_SYMBOL.findall(text))):
            if symbol not in llm_symbols:
                findings.append(Finding("absent_llm_symbol", surface, f"llm.{symbol}"))
        for hit in sorted({m.group(0) for m in _MODEL.finditer(text)}):
            model_tokens.add(hit)
            findings.append(Finding("model_reference", surface, hit))
        for name in sorted(set(_TOOL.findall(text))):
            tool_mentions.add(name)
            if name not in registered:
                findings.append(Finding("unregistered_tool", surface, name))

    scanned = {
        "surfaces": len(surfaces),
        "chars": sum(len(t or "") for t in surfaces.values()),
        "tool_mentions": len(tool_mentions),
        "flag_tokens": len(flag_tokens),
        "module_tokens": len(module_tokens),
        "model_tokens": len(model_tokens),
    }
    return findings, scanned


def unaccounted(collected: Collected) -> list:
    """Registered tools that were neither read nor recorded as a skip.

    The completeness equality, and the one bound here that cannot be tuned. A
    handler that silently disappears from a pass is how a gate becomes inert,
    so a pass that cannot account for every registered tool is not clean --
    it is incomplete, and says so.
    """
    read = {
        name[len("tool:") :] for name in collected.surfaces if name.startswith("tool:")
    }
    skipped = {
        s.surface[len("tool:") :]
        for s in collected.skips
        if s.surface.startswith("tool:")
    }
    return sorted(set(collected.registered) - read - skipped)


def floor_violations(scanned: Mapping, *, registered_count: int, missing=()) -> list:
    """Why this pass does not support a clean verdict, in report order."""
    out: list = []
    if missing:
        out.append(
            "registered tools neither read nor skipped: " + ", ".join(sorted(missing))
        )
    surfaces = int(scanned.get("surfaces", 0))
    if not surfaces:
        out.append("no text surface was read at all")
        return out
    chars = int(scanned.get("chars", 0))
    if chars < FLOOR_CHARS_PER_SURFACE * surfaces:
        out.append(f"chars={chars} < {FLOOR_CHARS_PER_SURFACE} x {surfaces} surfaces")
    mentions = int(scanned.get("tool_mentions", 0))
    want = max(
        FLOOR_TOOL_MENTIONS_MIN, int(registered_count) // FLOOR_TOOL_MENTION_DIVISOR
    )
    if mentions < want:
        out.append(f"tool_mentions={mentions} < {want}")
    flags = int(scanned.get("flag_tokens", 0))
    if flags < FLOOR_FLAG_TOKENS:
        out.append(f"flag_tokens={flags} < {FLOOR_FLAG_TOKENS}")
    return out


# ------------------------------------------------------------------- run ---- #


async def run(
    *,
    server: Any,
    settings_cls: Any,
    llm_module: Any,
    root: Path,
    ctx_factory: Callable | None = None,
    timeout: float = INVOKE_TIMEOUT_S,
) -> dict:
    """One full pass. Never raises; every failure becomes a finding or a skip."""
    async with _LOCK:
        collected = await collect(server, ctx_factory=ctx_factory, timeout=timeout)
    findings, scanned = scan(
        collected.surfaces,
        registered=collected.registered,
        live=live_names(settings_cls),
        llm_symbols=frozenset(dir(llm_module)),
        root=Path(root),
    )
    all_findings = list(collected.findings) + findings
    missing = unaccounted(collected)
    return {
        "schema": SCHEMA,
        "registered": sorted(collected.registered),
        # NAMES only, never reply TEXT: a reply that quoted 33k characters of
        # other replies back at a tester would be its own findability problem,
        # and every finding already names the surface it came from.
        "read": sorted(collected.surfaces),
        "scanned": scanned,
        "floors": floor_violations(
            scanned, registered_count=len(collected.registered), missing=missing
        ),
        "findings": [f.as_dict() for f in all_findings],
        "skips": [s.as_dict() for s in collected.skips],
        "egress": [
            {"surface": surface, "host": host}
            for surface, host in sorted(set(collected.egress))
        ],
    }


def render(report: Mapping) -> str:
    """The tester-facing reply. Markdown, and honest about what it did not do."""
    scanned = report.get("scanned") or {}
    findings = list(report.get("findings") or ())
    skips = list(report.get("skips") or ())
    floors = list(report.get("floors") or ())
    egress = list(report.get("egress") or ())
    lines = ["## Self-check — what this install's replies actually say", ""]
    if findings:
        lines.append(
            f"❌ **{len(findings)} finding(s).** A reply below names "
            "something this build cannot deliver, so a tester acting on it "
            "gets nothing."
        )
    elif floors:
        lines.append(
            "⚠️ **No findings, but the pass did not scan enough to "
            "mean it** — treat this as INCONCLUSIVE, not clean: " + ", ".join(floors)
        )
    else:
        lines.append("✅ **No findings.**")
    lines += [
        "",
        f"- Text surfaces read: **{scanned.get('surfaces', 0)}** "
        f"({scanned.get('chars', 0)} characters)",
        "- Tool names checked against this edition's registry: "
        f"**{scanned.get('tool_mentions', 0)}**",
        f"- Settings/credential names checked: **{scanned.get('flag_tokens', 0)}**",
        f"- Module names checked: **{scanned.get('module_tokens', 0)}**; model "
        f"references found (any is a finding): **{scanned.get('model_tokens', 0)}**",
    ]
    if findings:
        lines += ["", "### Findings"]
        for item in findings:
            lines.append(
                f"- **{item.get('kind')}** in `{item.get('surface')}`: "
                f"`{item.get('detail')}`"
            )
    if skips:
        lines += ["", f"### Not checked ({len(skips)})"]
        for item in skips:
            lines.append(f"- `{item.get('surface')}` — {item.get('reason')}")
    if egress:
        lines += [
            "",
            "### Outbound connections blocked during the pass",
            "Nothing left this machine; each attempt was refused and recorded.",
        ]
        lines += [
            f"- `{item.get('surface')}` → `{item.get('host')}`" for item in egress
        ]
    lines += [
        "",
        "_Every handler was called with its own declared defaults, so no "
        "acknowledgement and no `apply=true` was ever sent and no external "
        "write was possible._",
    ]
    return "\n".join(lines)
