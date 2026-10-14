"""Scan pass of the self-check: judge collected reply text against the live build."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from tools.selfcheck_patterns import (
    _LLM_SYMBOL,
    _MODEL,
    _MODULE,
    _SERVER_MODEL_CLAIM,
    _TOKEN,
    _TOOL,
)
from tools.selfcheck_types import Finding, ScanCtx


def _module_exists(root: Path, rel: str) -> bool:
    return any(
        (root / prefix / rel).exists() if prefix else (root / rel).exists()
        for prefix in ("", "tools", "agents")
    )


@dataclass
class _ScanTally:
    """The judging context of one scan and what it has seen so far."""

    registered: Any
    live: Any
    llm_symbols: Any
    root: Path
    findings: list = field(default_factory=list)
    tool_mentions: set = field(default_factory=set)
    flag_tokens: set = field(default_factory=set)
    module_tokens: set = field(default_factory=set)
    model_tokens: set = field(default_factory=set)


def _scan_flags(tally: _ScanTally, surface: str, text: str) -> None:
    for token in sorted(set(_TOKEN.findall(text))):
        if token.endswith("_"):  # a prefix reference, never a field name
            continue
        tally.flag_tokens.add(token)
        if token not in tally.live:
            tally.findings.append(Finding("dead_flag", surface, token))


def _scan_modules(tally: _ScanTally, surface: str, text: str) -> None:
    for rel in sorted(set(_MODULE.findall(text))):
        tally.module_tokens.add(rel)
        if not _module_exists(tally.root, rel):
            tally.findings.append(Finding("dead_module", surface, rel))


def _scan_llm_symbols(tally: _ScanTally, surface: str, text: str) -> None:
    for symbol in sorted(set(_LLM_SYMBOL.findall(text))):
        if symbol not in tally.llm_symbols:
            tally.findings.append(
                Finding("absent_llm_symbol", surface, f"llm.{symbol}")
            )


def _scan_model_claims(tally: _ScanTally, surface: str, text: str) -> None:
    for kind, pattern in (
        ("model_reference", _MODEL),
        ("server_model_claim", _SERVER_MODEL_CLAIM),
    ):
        for hit in sorted({m.group(0) for m in pattern.finditer(text)}):
            tally.model_tokens.add(hit)
            tally.findings.append(Finding(kind, surface, hit))


def _scan_tools(tally: _ScanTally, surface: str, text: str) -> None:
    for name in sorted(set(_TOOL.findall(text))):
        tally.tool_mentions.add(name)
        if name not in tally.registered:
            tally.findings.append(Finding("unregistered_tool", surface, name))


def _scan_surface(tally: _ScanTally, surface: str, text: str) -> None:
    _scan_flags(tally, surface, text)
    _scan_modules(tally, surface, text)
    _scan_llm_symbols(tally, surface, text)
    _scan_model_claims(tally, surface, text)
    _scan_tools(tally, surface, text)


def _tally_for(ctx: ScanCtx) -> _ScanTally:
    return _ScanTally(
        registered=ctx.registered,
        live=ctx.live,
        llm_symbols=ctx.llm_symbols,
        root=ctx.root,
    )


def _scanned(surfaces: Mapping, tally: _ScanTally) -> dict:
    return {
        "surfaces": len(surfaces),
        "chars": sum(len(t or "") for t in surfaces.values()),
        "tool_mentions": len(tally.tool_mentions),
        "flag_tokens": len(tally.flag_tokens),
        "module_tokens": len(tally.module_tokens),
        "model_tokens": len(tally.model_tokens),
    }


# Judge collected text. Returns ``(findings, scanned)``.
#
# ``scanned`` is not decoration. Every check here asserts an ABSENCE, and an
# absence assertion passes for free the moment its detector stops matching --
# so each one reports how many candidates it actually saw, and the tests turn
# those counts into floors.
def scan(surfaces: Mapping, ctx: ScanCtx) -> tuple:
    """Judge collected text. Returns ``(findings, scanned)``."""
    tally = _tally_for(ctx)
    for surface in sorted(surfaces):
        _scan_surface(tally, surface, surfaces[surface] or "")
    return tally.findings, _scanned(surfaces, tally)
