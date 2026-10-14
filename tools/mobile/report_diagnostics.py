"""Diagnostics rendering for the report: device facts, performance, findings, accessibility, environment, logs, capture tables and the about section."""

from __future__ import annotations

from tools.mobile.rdiag_a11y import _a11y_lede, _a11y_section  # noqa: F401
from tools.mobile.rdiag_a11yrows import _a11y_rows, _a11y_screen_row  # noqa: F401
from tools.mobile.rdiag_about import PAGE_LIMITS, _about_html  # noqa: F401
from tools.mobile.rdiag_capture import (  # noqa: F401
    CAPTURE_FIELDS,
    _capture_table,
    _capture_value,
)
from tools.mobile.rdiag_device import (  # noqa: F401
    DEVICE_KIND_UNKNOWN,
    DEVICE_KINDS,
    _app_label,
    _device_sub,
    _locale_cell,
)
from tools.mobile.rdiag_env import (  # noqa: F401
    SYSTEM_IMAGE_ABSENT,
    _env_host_row,
    _env_row,
    _env_section,
)
from tools.mobile.rdiag_facts import (  # noqa: F401
    _facts_strip,
    _lease_cells,
    _run_cells,
)
from tools.mobile.rdiag_findings import (  # noqa: F401
    _findings_lede,
    _findings_section,
    _findings_table,
    _replayed_turns,
    _spoken_turns,
)
from tools.mobile.rdiag_logs import _logs_section  # noqa: F401
from tools.mobile.rdiag_page import (  # noqa: F401
    _diag_head,
    _diag_tail,
    _diagnostics_html,
)
from tools.mobile.rdiag_part import (  # noqa: F401
    _diag_part,
    _evidence_html,
    _has_network,
    _network_html,
    _timings_part,
)
from tools.mobile.rdiag_perf import (  # noqa: F401
    _perf,
    _perf_figure,
    _perf_legend,
    _perf_run_rows,
    _perf_tile,
    _perf_tiles,
)
