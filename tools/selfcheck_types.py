"""Value types of the self-check: findings, skips, the collected pass, fixtures and parameter objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from tools.selfcheck_consts import INVOKE_TIMEOUT_S, SELF_TOOL_NAME


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


@dataclass
class Collected:
    """Everything one pass observed, before any judgement is applied."""

    surfaces: dict
    skips: list
    findings: list
    egress: list
    registered: frozenset


@dataclass(frozen=True)
class CollectOpts:
    """Knobs of one collection sweep (all defaulted)."""

    ctx_factory: Callable | None = None
    timeout: float = INVOKE_TIMEOUT_S
    self_name: str = SELF_TOOL_NAME


@dataclass(frozen=True)
class ToolJob:
    """One tool to invoke and the options of the sweep it belongs to."""

    tool: Any
    opts: CollectOpts


@dataclass(frozen=True)
class ScanCtx:
    """What :func:`tools.selfcheck.scan` judges the surfaces against."""

    registered: Any
    live: Any
    llm_symbols: Any
    root: Path


@dataclass(frozen=True)
class RunReq:
    """Everything :func:`tools.selfcheck.run` needs, injected by the caller."""

    server: Any
    settings_cls: Any
    llm_module: Any
    root: Path
    opts: CollectOpts = field(default_factory=CollectOpts)
