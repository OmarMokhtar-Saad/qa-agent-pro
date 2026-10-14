"""Learning stage flows: a clean explore run becomes a replayable app flow.

When an explore run ended with ``goal_reached`` (or a pass verdict) and its last step
ran as planned, the steps that worked in each app are saved as one flow per app, named
from the normalised goal (``flows.json``, via ``app_flows``). Typed values never enter
it: a value is a ``{field}`` placeholder, a secret step keeps only its field name.
The flow is offered to the next run by ``knowledge_plug_flows``. Never raises.
"""

from __future__ import annotations

import logging

from tools.mobile import app_flows, kstg_flow_build
from tools.mobile.knowledge_learn import StageResult

logger = logging.getLogger(__name__)


def _save(package: str, name: str, acts: list) -> str:
    """``""`` once the flow is saved, else why it was not."""
    got = app_flows.template_from_script({"actions": acts})
    if got.get("error"):
        return str(got["error"])[:120]
    saved = app_flows.save_flow(package, name, got["content"])
    return str(saved.get("error") or "")[:120]


def _packages(lc, app: str) -> list:
    return sorted({p for p in (lc.package, app) if p})


def run(conn, lc) -> StageResult:
    goal, app = kstg_flow_build.reached(lc.run_id)
    if not goal or not kstg_flow_build.ended_clean(lc.steps):
        return StageResult("flows", notes=["no clean goal_reached run"])
    name = kstg_flow_build.flow_name(goal)
    result = StageResult("flows")
    for package in _packages(lc, app):
        acts = kstg_flow_build.app_actions(lc.steps, package)
        if len(acts) < kstg_flow_build.MIN_STEPS:
            continue
        why = _save(package, name, acts)
        result.wrote += 0 if why else 1
        result.notes.append("%s: %s" % (package, why or "saved " + name))
    return result
