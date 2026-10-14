"""The qa-doctor run state: report-head tuple and the gathered run (moved from setup_check)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger("tools.mcp_handlers")


class _SetupHead(NamedTuple):
    verdict: str
    app_version: str
    restart_note: str
    reload_note: str
    heal_lines: list
    atlassian_lines: list
    py_version: str
    py_ok: bool
    export_line: str
    jira_used: bool
    jira_status_line: str
    tool_paths: dict


@dataclass
class _SetupRun:
    """Everything one qa-doctor run learns before the report is rendered.

    Findings are classified as blocking / recommended / optional so the report
    opens with a single actionable verdict; ``limited`` names what this machine
    cannot do and is appended to the headline, never counted.
    """

    blockers: list[str] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    limited: list[str] = field(default_factory=list)
    # THE ONE LOOKUP for the optional binaries, read twice -- by the
    # Command-line tooling rows and by `limited` -- and derived once.
    tool_paths: dict = field(default_factory=dict)
    mobile_state: str = ""
    verdict: str = ""
    app_version: str = ""
    restart_note: str = ""
    reload_note: str = ""
    heal_lines: list = field(default_factory=list)
    atlassian_lines: list = field(default_factory=list)
    py_version: str = ""
    py_ok: bool = True
    export_line: str = ""
    jira_used: bool = False
    jira_status_line: str = ""
    # The workspace roots and the --fix switch of this run, plus the Atlassian
    # verdict view once loaded (moved here so helpers take ONE run argument).
    workspace_roots: list[Path] | None = None
    fix: bool = False
    view: dict | None = None

    def head(self) -> _SetupHead:
        """The report-head tuple, in _SetupHead field order."""
        return _SetupHead(
            self.verdict,
            self.app_version,
            self.restart_note,
            self.reload_note,
            self.heal_lines,
            self.atlassian_lines,
            self.py_version,
            self.py_ok,
            self.export_line,
            self.jira_used,
            self.jira_status_line,
            self.tool_paths,
        )
