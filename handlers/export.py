"""Moved from handlers (refactor P2): export."""

from __future__ import annotations

from typing import Callable

from handlers import explore_sessions as _explore_sessions
from handlers.export_auto import (  # noqa: F401
    AutoExportRequest,
    _auto_export_reply,
    _auto_export_write,
    _export_destination,
    _hand_on_path,
    _ready_text,
)
from handlers.export_auto import _auto_export_xlsx as auto_export_xlsx  # noqa: F401
from handlers.export_auto_dir import (  # noqa: F401
    _accept_answer,
    _ask_folder,
    _auto_export_ask_dir,
    _auto_export_target,
    _budget_note,
    _timeout_note,
    _unanswered_dir_note,
    _workbook_name,
)
from handlers.export_dir_notes import (  # noqa: F401
    _TEMP_EXPORT_LABEL,
    _WELL_KNOWN_FOLDERS,
    _config_line_note,
    _install_itself_note,
    _not_full_path_note,
    _outside_roots_note,
    _too_long_note,
    _unusable_note,
)
from handlers.export_dir_rules import (  # noqa: F401
    _MAX_EXPORT_DIR_CHARS,
    _allowed_roots,
    _hidden_folder_note,
    _resolve_elicited_dir,
    _safe_elicited_dir,
    _screen_elicited_dir,
)
from handlers.export_formats import (  # noqa: F401
    _available_exporters,
    _format_menu_markdown,
)
from handlers.export_handle import (  # noqa: F401
    _export_prep_id_reply,
    _export_suite_resolve,
    _load_and_export,
    _resolve_format,
    _resolve_suite_id,
    handle_export_suite,
)
from handlers.export_paths import _export_ticket_slug  # noqa: F401
from handlers.export_paths import (
    _resolved_export_dir as resolved_export_dir,  # noqa: F401
)
from handlers.export_probes import (  # noqa: F401
    _TEST_CASES_ONLY_NOTICE,
    _edition_facts,
)
from handlers.export_probes import (
    _code_changed_since_start as code_changed_since_start,  # noqa: F401
)
from handlers.export_probes import (
    _env_changed_since_start as env_changed_since_start,  # noqa: F401
)
from handlers.export_suite import (  # noqa: F401
    _MOVE_FAILED_NOTE,
    ExportJob,
    ExportOptions,
    _export_done,
    _export_suite_reply,
    _relocate_export,
)
from tools.csv_exporter import generate_test_case_csv
from tools.gherkin_exporter import generate_feature_file
from tools.playwright_exporter import generate_playwright_script
from tools.suite_store import load_suite  # noqa: F401
from tools.testrail_exporter import generate_testrail_csv
from tools.xlsx_generator import generate_test_case_xlsx

# In-process exploratory-session store keyed by the caller-supplied session_id.
# The MCP server is a long-running stdio process, so this dict persists across
# tool calls within one session; bounded by handlers/explore_sessions.py.
_SESSIONS = _explore_sessions.SESSIONS

# Each entry is a thin lambda (not a direct function reference) so the
# exporter is looked up from this module's global namespace at *call* time.
# A direct reference (`"csv": generate_test_case_csv`) would bind the
# original function object at import time, silently defeating
# `monkeypatch.setattr(mcp_handlers, "generate_test_case_csv", ...)` in tests
# (and any future runtime patching) because the dict would keep pointing at
# the pre-patch function.
_EXPORTERS: dict[str, Callable] = {
    "csv": lambda s: generate_test_case_csv(s),
    "xlsx": lambda s: generate_test_case_xlsx(s),
    "gherkin": lambda s: generate_feature_file(s),
    "playwright": lambda s: generate_playwright_script(s),
    "testrail": lambda s: generate_testrail_csv(s),
}


# How to finalize, in one place.
#
# Five separate replies used to spell this out, and with the duplicate review
# ON, four of them named the route that FORFEITS it and never mentioned the
# sidecar -- one calling it "the route here". Each was correct when written
# and none was wrong in isolation; the defect was that five authors could
# disagree. agents/host_*.py owns which route is right (it gets both seam
# states right, and _FINALIZE_SENTINEL exists because run3/TICKET-5645 lost the
# review across 98 cases by leading with the empty one), so callers ask here
# rather than stating it.
#
# ``short`` gives the clause form for mid-sentence use. Never raises.
def _finalize_route_phrase(short: bool = False) -> str:
    """How to finalize, in one place: the review-keeping sidecar route."""
    clause = (
        "a small JSON SIDECAR carrying `duplicate_groups` (an empty list if "
        "you found none) and NO test_cases -- that KEEPS the duplicate "
        "review; an EMPTY `suite_json` also finalizes but FORFEITS it"
    )
    return clause if short else f"finalize with `qa_submit_suite` and {clause}"


# Public seams above (tests patch ``auto_export_xlsx``, ``resolved_export_dir``,
# ``code_changed_since_start``, ``env_changed_since_start``). The private names
# are call-through aliases for callers in other waves; WX drops them.
