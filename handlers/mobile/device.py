"""Moved from handlers (refactor P2): mobile_device.

The handlers live in ``handlers.mobile.device_*``; this module re-exports them
(the patch surface the tests and ``handlers.mobile.tools_*`` read) and keeps
``handle_network_watch``, which reads the facade attributes at call time.
``handle_list_devices`` lives in ``handlers.device_list`` (it ships without the lane).
"""

from __future__ import annotations

import json
import logging

from handlers import _common
from handlers.mobile.device_apps import (  # noqa: F401
    handle_app_info,
    handle_mobile_stop,
    handle_update_app,
)
from handlers.mobile.device_capture import (  # noqa: F401
    _setup_capture_prepare,
    _setup_capture_remove,
    _setup_capture_status,
    handle_setup_capture,
)
from handlers.mobile.device_requests import WatchArgs
from handlers.mobile.device_runs import (  # noqa: F401
    _mobile_run_row,
    _mobile_status_listing,
)
from handlers.mobile.device_status import (  # noqa: F401
    _mobile_status_report,
    _mobile_status_text,
    handle_mobile_status,
)
from tools.mobile_evidence import network_watch

logger = logging.getLogger("tools.mcp_handlers")


# Thin by construction: `tools/mobile_evidence/network_watch.py` only gives its
# own package's EXISTING console capture a run-free entry point. Nothing about
# how traffic is observed, parsed or redacted is decided here or in the
# desktop app.
async def handle_network_watch(args: WatchArgs | None = None) -> str:
    """Passive, root-free network watch for a GUI client. Never raises."""
    request = args or WatchArgs()
    await _common.audit("mcp_network_watch")
    try:
        payload = await network_watch.handle(
            request.action,
            network_watch.WatchTarget(
                request.serial, request.package, bool(request.apply)
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("handle_network_watch failed")
        payload = {"error": str(exc), "content": None}
    return "```json\n" + json.dumps(payload, indent=2, default=str) + "\n```"
