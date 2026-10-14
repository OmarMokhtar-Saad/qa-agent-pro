"""Part of the mobile_run split (see handlers/mobile/run.py): route."""

from __future__ import annotations

import tools.mobile.session_audit as session_audit
import tools.mobile.session_core as session_core
from handlers import _common
from handlers.mobile.run_hooks import mobile_run
from handlers.mobile.run_request import _MobileTestRequest
from handlers.mobile.run_resume import _mobile_resume
from tools.mobile.session_audit import AuditFacts

# ---------------------------------------------------------------------------
# Notes for ``handle_mobile_test`` (moved out of the function to keep it short)
#
# The state machine itself lives in the ``tools/mobile/session_*.py`` modules and every
# string in ``tools/mobile/render.py`` -- the handler gates, delegates,
# renders and audits, and holds no state of its own. That matters because the
# MCP process restarts on every code and `.env` edit: any state kept there
# would be lost by exactly the event that makes a tester resume.
#
# Two independent guards, like ``handle_push_suite``: the lane's kill-switch
# AND ``apply=true`` on any step that installs, downloads or launches. A
# flag-OFF call carrying ``apply=true`` REFUSES BY NAME rather than quietly
# doing a dry run, because a success-shaped reply for a step that never ran is
# the worse failure.
#
# Every ``tools.mobile`` import is function-local, exactly as
# ``handle_push_suite`` imports its pushers: those modules are absent from the
# distribution build, so a module-scope import would break it at import time
# on an install no test in this repo can observe.
#
# tools.untrusted, NOT agents.api_test_agent: `_safe` there is a bare alias
# of `single_line` (api_test_agent.py:423) and that module is deliberately
# absent from scripts/build_dist.AGENT_FILES. The import was harmless only
# while the lane could not register on a distribution build; fixing that
# (2026-09-04) made all three mobile tools raise ModuleNotFoundError on a
# dist -- confirmed by calling them on the published v1.77.0 install.
# ---------------------------------------------------------------------------

# ``call`` is ``(run_id, session_token, continue_run)``, packed by the facade so the
# route takes three parameters.


async def _mobile_test_route(
    request: _MobileTestRequest, call: tuple[str, str, bool]
) -> str:
    """Audit the call, then resume an existing run or start a new one."""
    budget = await _mobile_route_audit(request, call[0])
    if call[0]:
        return await _mobile_resume(call, budget, request.progress)
    return await mobile_run._mobile_new_run(request)


async def _mobile_route_audit(request: _MobileTestRequest, run_id: str) -> object:
    """Mint the call's budget and audit the call; return the budget."""

    budget = session_core.new_budget()
    step = request.source or "menu"
    detail = session_audit.audit_detail(
        AuditFacts(run_id=run_id, step=step, apply=request.apply)
    )
    await _common.audit("mcp_mobile_test", entity_id=run_id or None, detail=detail)
    return budget
