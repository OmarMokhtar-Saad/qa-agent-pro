"""The goal a passed host-lane case saves its flow under (see ``kstg_flow_build``).

A host manifest has no explore block, so no goal text exists; the case title stands
in for it. Only a run of exactly ONE passed case is saved: a second case relaunches
the app, and the steps of both in one route would not replay.
"""

from __future__ import annotations

from tools.mobile import run_store

PASS = "pass"


def goal(run_id: str, manifest: dict) -> str:
    """The title of the single passed case of a host run, else ``""``."""
    if str(manifest.get("lane") or "") == "explore":
        return ""
    cases = (run_store.list_cases(run_id) or {}).get("content") or []
    if len(cases) != 1 or str(cases[0].get("verdict") or "") != PASS:
        return ""
    return str(cases[0].get("title") or "")
