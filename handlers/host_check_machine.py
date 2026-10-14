"""Machine-report and self-check handlers."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import llm
from handlers import _common, export
from tools import machine_report, selfcheck, settings_port

logger = logging.getLogger("tools.mcp_handlers")

_SELFCHECK_FAILED = (
    "⚠️ The self-check could not complete, so it has NO "
    "verdict for you -- do not read this as clean. The failure is in "
    "this install's log (`data/logs/`). Nothing was changed."
)


async def handle_machine_report(section: str = "all") -> str:
    """The machine-readable twin of the prose surfaces, for a non-chat client.

    READ-ONLY, unlike `qa-doctor`: it repairs no `.env` and writes no MCP
    config, so a desktop app may poll it without a tester wondering what a
    refresh changed. Never raises -- an unreadable producer becomes an
    `undetermined` row, because a client that loses the whole report over one
    unreadable file learns less than one that loses a line.
    """
    await _common.audit("mcp_machine_report")
    try:
        payload = await asyncio.to_thread(
            machine_report.collect, section, edition_probe=export._edition_facts
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("handle_machine_report failed")
        payload = {"error": str(exc)}
    return "```json\n" + json.dumps(payload, indent=2, default=str) + "\n```"


def _selfcheck_detail(report: dict) -> dict:
    return {
        "findings": len(report.get("findings") or ()),
        "skips": len(report.get("skips") or ()),
        "scanned": report.get("scanned") or {},
        "floors": report.get("floors") or [],
    }


# Invoke every registered handler and report what the replies NAME.
#
# The composition root for ``tools/selfcheck.py``: this is the only place
# that can supply the LIVE server object, because the Constitution forbids a
# ``tools/`` module from importing ``mcp_server`` and ``qa_selfcheck``'s
# closure is the one thing holding a reference to the built ``FastMCP``.
# Everything the check needs is injected here and nothing is discovered by
# import.
#
# Read-only for the tester's data and structurally incapable of an external
# write -- see the four layers documented in ``tools/selfcheck``. It answers
# the question ``qa-doctor`` cannot: ``qa-doctor`` reports whether this
# MACHINE is ready, while this reports whether this BUILD's own replies still
# describe it. Never raises.
async def handle_selfcheck(*, server: Any) -> str:
    """Invoke every registered handler and report what the replies NAME."""
    await _common.audit("mcp_selfcheck")
    try:
        report = await selfcheck.run(
            selfcheck.RunReq(
                server=server,
                settings_cls=type(settings_port.get()),
                llm_module=llm,
                root=_common.INSTALL_ROOT,
            )
        )
        await _common.audit("mcp_selfcheck_result", detail=_selfcheck_detail(report))
        return selfcheck.render(report)
    except Exception:
        logger.exception("self-check failed")
        return _SELFCHECK_FAILED
