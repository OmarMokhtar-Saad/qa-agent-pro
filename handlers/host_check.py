"""Moved from handlers (refactor P2): host_check.

Facade: the code lives in the ``host_check_*`` leaves; every name a caller or
a test imports or patches is re-exported here.
"""

from __future__ import annotations

import logging
import sys  # noqa: F401  (tests patch host_check.sys)

from handlers.host_check_acfield import (  # noqa: F401
    _ac_field_configured_lines,
    _ac_field_discovery_line,
    ac_field_section,
)
from handlers.host_check_jira import (  # noqa: F401
    _atlassian_report,
    atlassian_autofix,
    jira_in_use,
)
from handlers.host_check_jstatus import (  # noqa: F401
    _JIRA_STATUS_TAIL,
    _JIRA_STATUS_TEMPLATES,
    _JIRA_STATUS_UNCHECKED,
    _jira_status_age,
    jira_status_text,
)
from handlers.host_check_machine import (  # noqa: F401
    handle_machine_report,
    handle_selfcheck,
)
from handlers.host_check_report import (  # noqa: F401
    _host_check_header,
    _host_check_step_lines,
    handle_host_check,
)
from handlers.host_check_toolinfo import _TOOL_INFO  # noqa: F401
from handlers.host_check_tools import (  # noqa: F401
    _binary_line,
    optional_tool_paths,
    tooling_gaps,
    tooling_lines,
)
from handlers.host_check_verdict import (  # noqa: F401
    VerdictCounts,
    overall_verdict,
    update_rate_limit_advisory,
)
from tools.jira_mcp import ac_field_discovery_on  # noqa: F401

logger = logging.getLogger("tools.mcp_handlers")
