"""Jira MCP: tables helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging

logger = logging.getLogger("tools.jira_mcp")


# Markdown table rows -> readable prose lines. Never raises.
#
# Jira use-case descriptions here are almost entirely tables, and truncating
# one to a few hundred characters yields cut-off pipe syntax that grounds
# nothing ("| **Post-condition** | User view store"). Collapsing each row to
# "cell - cell" first means the SAME budget carries real requirement text.
# Separator rows (|---|---|) are dropped; non-table text passes through.
def _flatten_table_text(text: str) -> str:
    """Markdown table rows -> readable prose lines. Never raises."""
    try:
        out: list[str] = []
        for line in str(text or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("|"):
                cells = [c.strip(" *_\t") for c in stripped.strip("|").split("|")]
                cells = [c for c in cells if c and set(c) - set("-: ")]
                if not cells:
                    continue  # a |---|---| separator row
                out.append(" - ".join(cells))
                continue
            out.append(stripped)
        return "\n".join(out).strip()
    except Exception:
        logger.exception("Flattening markdown tables failed - using raw text")
        return str(text or "")
