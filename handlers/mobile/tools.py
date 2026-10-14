"""MCP registration adapters: mobile_tools.

The tools are registered by the one-tool registrars in ``handlers.mobile_tools_*``;
this facade keeps the mobile-lane gate and the call that registers them.
"""

from handlers import _common
from handlers.mobile.tools_device import (
    _register_mobile_app_info_tool,
    _register_mobile_stop_tool,
    _register_mobile_update_app_tool,
)
from handlers.mobile.tools_drive import _register_mobile_test_tool
from handlers.mobile.tools_know import (
    _register_mobile_flows_tool,
    _register_mobile_knowledge_tool,
    _register_mobile_notes_tool,
)
from handlers.mobile.tools_step import _register_mobile_submit_step_tool
from handlers.mobile.tools_watch import (
    _register_mobile_capture_setup_tool,
    _register_mobile_network_watch_tool,
    _register_mobile_status_tool,
)


# The mobile emulator lane. ONE call, and nothing else may join it here:
# `mobile_lane_enabled()` is `mobile_modules_present()` alone, which
# checks tools/mobile is really on disk -- a build made without the pinned
# IME ships none of it. QA_MOBILE_HTTPS_CAPTURE_ENABLED is not a term: it gates only
# the capture download and the capture-certificate install, at those effects.
# tests/mobile/test_mobile_registration.py parses this file and fails if
# this `if` becomes anything other than a call to that predicate.
#
# It carried a THIRD term, `not test_cases_only()`, until 2026-09-04. That
# is an edition label decided by whether the bug-report and coach agents
# shipped, and it made the lane unreachable on every distribution build:
# v1.77.0 registered none of these three tools while its own README
# promised all three. Do not re-add it here or in the predicate.
def register_mobile_tools(mcp, kit, rt) -> None:
    """Register the mobile emulator lane tools when its modules are present."""
    if _common.mobile_lane_enabled():
        _register_mobile_stop_tool(mcp, kit.context, rt)
        _register_mobile_app_info_tool(mcp, kit.context, rt)
        _register_mobile_update_app_tool(mcp, kit.context, rt)
        _register_mobile_test_tool(mcp, kit, rt)
        _register_mobile_submit_step_tool(mcp, kit, rt)
        _register_mobile_status_tool(mcp, kit.context, rt)
        _register_mobile_network_watch_tool(mcp, kit.context, rt)
        _register_mobile_notes_tool(mcp, kit.context, rt)
        _register_mobile_knowledge_tool(mcp, kit.context, rt)
        _register_mobile_flows_tool(mcp, kit.context, rt)
        _register_mobile_capture_setup_tool(mcp, kit.context, rt)
