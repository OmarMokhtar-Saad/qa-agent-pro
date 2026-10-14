"""Collection pass of the self-check: invoke every tool, read every static tester-facing text."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

from config.settings_guard import never_raise
from tools import capabilities
from tools.selfcheck_egress import egress_guard
from tools.selfcheck_payload import build_payload
from tools.selfcheck_types import (
    Collected,
    CollectOpts,
    Finding,
    FixtureContext,
    Skip,
    ToolJob,
)

logger = logging.getLogger("tools.selfcheck")

_NO_FIXTURE = "no fixture value for required parameter(s): "
_SELF_SKIP = "the self-check itself; invoking it would recurse"


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


def _invocation_error(out: Collected, key: str, exc: Exception) -> None:
    logger.warning("self-check: %s raised", key, exc_info=True)
    out.findings.append(
        Finding("invocation_error", key, f"{type(exc).__name__}: {exc}"[:300])
    )


async def _collect_tool(out: Collected, key: str, job: ToolJob) -> None:
    fn = getattr(job.tool, "fn", None)
    if not callable(fn):
        out.skips.append(Skip(key, "the registry entry exposes no callable"))
        return
    kwargs, unseedable = build_payload(getattr(job.tool, "parameters", None))
    if unseedable:
        out.skips.append(Skip(key, _NO_FIXTURE + ", ".join(unseedable)))
        return
    if _accepts_ctx(fn):
        kwargs["ctx"] = (job.opts.ctx_factory or FixtureContext)()
    # never raise to the caller
    with never_raise() as g:
        result = await asyncio.wait_for(_maybe_await(fn(**kwargs)), job.opts.timeout)
    if g.failed:
        assert isinstance(g.exc, Exception)
        _invocation_error(out, key, g.exc)
        return
    out.surfaces[key] = _as_text(result)


async def _collect_prompt(out: Collected, key: str, prompt: Any) -> None:
    fn = getattr(prompt, "fn", None)
    if not callable(fn):
        out.skips.append(Skip(key, "the registry entry exposes no callable"))
        return
    # never raise to the caller
    with never_raise() as g:
        out.surfaces[key] = _as_text(await _maybe_await(fn()))
    if g.failed:
        assert isinstance(g.exc, Exception)
        _invocation_error(out, key, g.exc)


@dataclass
class _Sweep:
    out: Collected
    opts: CollectOpts
    at: dict


async def _collect_tools(sweep: _Sweep, tools: dict) -> None:
    for name in sorted(tools):
        key = f"tool:{name}"
        sweep.at["surface"] = key
        if name == sweep.opts.self_name:
            sweep.out.skips.append(Skip(key, _SELF_SKIP))
            continue
        await _collect_tool(sweep.out, key, ToolJob(tools[name], sweep.opts))


async def _collect_prompts(sweep: _Sweep, prompts: dict) -> None:
    for name in sorted(prompts):
        key = f"prompt:{name}"
        sweep.at["surface"] = key
        await _collect_prompt(sweep.out, key, prompts[name])


def _read_instructions(out: Collected, server: Any) -> None:
    instructions = getattr(server, "instructions", "") or ""
    if instructions:
        out.surfaces["instructions"] = instructions
    else:
        out.skips.append(
            Skip("instructions", "this server exposes no instructions block")
        )


async def collect(server: Any, opts: CollectOpts | None = None) -> Collected:
    """Invoke every registered tool and read every static tester-facing text."""
    opts = opts or CollectOpts()
    tools = await capabilities.tools_by_name(server)
    prompts = await capabilities.prompts_by_name(server)
    out = Collected(
        surfaces={}, skips=[], findings=[], egress=[], registered=frozenset(tools)
    )
    _read_instructions(out, server)
    sweep = _Sweep(out, opts, {"surface": ""})
    with egress_guard(out.egress, lambda: sweep.at["surface"]):
        await _collect_tools(sweep, tools)
        await _collect_prompts(sweep, prompts)
    return out
