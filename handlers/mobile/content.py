"""Moved from handlers (refactor P2): mobile_content.

Facade: the code lives in the ``mobile_content_*`` leaves; every old name stays here."""

from __future__ import annotations

import logging

from handlers.mobile.content_args import (  # noqa: F401
    _FLOW_WARN,
    _mobile_arg,
    _mobile_flow_names,
    _mobile_prepare_target,
    _pop_flow_route_args,
    _with_script,
)
from handlers.mobile.content_finding import (  # noqa: F401
    _mobile_fold_finding,
    _mobile_merge_finding,
)
from handlers.mobile.content_flow import (  # noqa: F401
    _flow_prepare_checks,
    _mobile_flow_finish,
    _mobile_flow_prepare,
)
from handlers.mobile.content_flows import handle_mobile_flows  # noqa: F401
from handlers.mobile.content_knowledge import (  # noqa: F401
    handle_mobile_knowledge,
    handle_mobile_notes,
)
from handlers.mobile.content_manage import (  # noqa: F401
    _mobile_flows_action,
    _mobile_routes_manage,
)
from handlers.mobile.content_requests import (  # noqa: F401
    FlowsRequest,
    KnowledgeRequest,
    NotesRequest,
)
from handlers.mobile.content_route import (  # noqa: F401
    _mobile_route_finish,
    _mobile_route_prepare,
    _route_prepare_checks,
)
from handlers.mobile.content_run import (  # noqa: F401
    _mobile_avd_reply,
    _mobile_note_intake_line,
    _mobile_parse_env,
    handle_mobile_test_content,
)
from handlers.mobile.content_step import (  # noqa: F401
    _mobile_note_line,
    _mobile_plan_flow_route,
    _mobile_plan_lines,
    _mobile_plan_one,
    handle_submit_mobile_step_content,
)

logger = logging.getLogger("tools.mcp_handlers")
