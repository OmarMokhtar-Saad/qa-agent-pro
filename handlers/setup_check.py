"""The qa-doctor facade: every name callers or tests reach through here is re-exported.

The implementation lives in the flat `handlers/setup_*.py` modules. A test that patches
a name patches it on its home module (the one whose code looks it up at call time).
"""

from __future__ import annotations

from handlers.setup_gates import setup_gate_lines  # noqa: F401
from handlers.setup_handler import (  # noqa: F401
    _setup_mobile_lines,
    handle_setup_check,
    logger,
)
from handlers.setup_jira import connect_hint_line  # noqa: F401
from handlers.setup_mobile_off import mobile_off_attached_note  # noqa: F401
from handlers.setup_reload_gate import (  # noqa: F401
    _DIST_UPDATE_REPO,
    RELOAD_HEADLINES,
    pending_reload_reason,
    reloading_message,
    run_reload_gate,
    schedule_reload,
)
from handlers.setup_reload_marker import (  # noqa: F401
    _RELOAD_MARKER_NAME,
    _RELOAD_MARKER_TTL_S,
    _write_reload_marker,
    consume_reload_marker,
    peek_reload_marker,
)
from handlers.setup_reload_report import drift_advisory, reload_outcome  # noqa: F401
