"""One full pass of the self-check: collect, scan, judge floors, build the report."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tools.selfcheck_collect import collect
from tools.selfcheck_consts import SCHEMA
from tools.selfcheck_floor import floor_violations, unaccounted
from tools.selfcheck_live import live_names
from tools.selfcheck_scan import scan
from tools.selfcheck_types import Collected, RunReq, ScanCtx

_LOCK = asyncio.Lock()


def _scan_ctx(req: RunReq, collected: Collected) -> ScanCtx:
    return ScanCtx(
        registered=collected.registered,
        live=live_names(req.settings_cls),
        llm_symbols=frozenset(dir(req.llm_module)),
        root=Path(req.root),
    )


# NAMES only, never reply TEXT: a reply that quoted 33k characters of
# other replies back at a tester would be its own findability problem,
# and every finding already names the surface it came from.
def _report(collected: Collected, findings: list, scanned: dict) -> dict:
    return {
        "schema": SCHEMA,
        "registered": sorted(collected.registered),
        "read": sorted(collected.surfaces),
        "scanned": scanned,
        "floors": floor_violations(
            scanned,
            registered_count=len(collected.registered),
            missing=unaccounted(collected),
        ),
        "findings": [f.as_dict() for f in list(collected.findings) + findings],
        "skips": [s.as_dict() for s in collected.skips],
        "egress": [
            {"surface": surface, "host": host}
            for surface, host in sorted(set(collected.egress))
        ],
    }


async def run(req: RunReq) -> dict:
    """One full pass. Never raises; every failure becomes a finding or a skip."""
    async with _LOCK:
        collected = await collect(req.server, req.opts)
    findings, scanned = scan(collected.surfaces, _scan_ctx(req, collected))
    return _report(collected, findings, scanned)
