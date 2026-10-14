"""The ``qa_mobile_flows`` handler: list, show or delete saved flows or routes."""

from __future__ import annotations

import logging

from handlers import _common, mobile_gate
from handlers.mobile import step as mobile_step
from handlers.mobile.content_args import _FLOW_WARN
from handlers.mobile.content_manage import _mobile_flows_action, _mobile_routes_manage
from handlers.mobile.content_requests import FlowsRequest
from tools.untrusted import single_line

logger = logging.getLogger("tools.mcp_handlers")


async def _flows_dispatch(verb: str, pkg: str, req: FlowsRequest) -> str:
    """Route *verb* to the flow store or, for ``kind='route'``, the route store."""
    kind_name = str(req.kind or "flow").strip().lower()
    if kind_name not in ("flow", "route"):
        return _FLOW_WARN + "`kind` must be `flow` or `route`."
    if kind_name == "route":
        return await _mobile_routes_manage(verb, pkg, req.name)
    return await _mobile_flows_action(verb, pkg, req.name)


# ``qa_mobile_flows``: list, show or delete the flows (``kind='route'``: the routes) saved
# for one app. Never raises.
async def handle_mobile_flows(req: FlowsRequest) -> str:
    """``qa_mobile_flows``: list, show or delete the saved flows or routes."""
    if not _common.mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    try:
        verb = str(req.action or "list").strip().lower()
        if verb not in ("list", "show", "delete"):
            return _FLOW_WARN + "`action` must be `list`, `show` or `delete`."
        pkg, _rid, refusal = mobile_step._mobile_note_target(req.run_id, req.package)
        if refusal:
            return _FLOW_WARN + single_line(refusal, 300) + "."
        return await _flows_dispatch(verb, pkg, req)
    except Exception:
        logger.exception("mobile flows failed")
        return _FLOW_WARN + "the flow store is unavailable."
