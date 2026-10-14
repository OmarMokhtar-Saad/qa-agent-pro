"""The export-format map and its menu (moved out of handlers.export)."""

from __future__ import annotations

from typing import Callable

from handlers.export_hooks import export


# The export-format map for this call.
#
# Built from the module-global ``_EXPORTERS`` at CALL time, so
# ``monkeypatch.setitem(mcp_handlers._EXPORTERS, ...)`` keeps working. Never
# raises.
#
# 2026-08-15 (dead-code deletion batch D4): this used to take ``story_key`` /
# ``output_dir`` and add a sixth, gate-controlled ``zephyr`` entry -- the only
# exporter that wrote a PAIR into a directory rather than a single file, and
# the only reason those two parameters existed. ``tools/zephyr_exporter.py``
# is DELETED, so the map is now exactly ``_EXPORTERS`` and the parameters went
# with the entry instead of being kept as arguments nothing reads.
#
# NOTE: mcp_server.py's qa_export_suite docstring is the only format list an
# MCP client ever sees, so it must stay a superset of these keys.
def _available_exporters() -> dict[str, Callable]:
    """The export-format map for this call."""
    return dict(export._EXPORTERS)


def _format_menu_markdown() -> str:
    return (
        "## Export format\n\n"
        "Call `qa_export_suite` with one of these as `format`:\n"
        + "\n".join(f"- `{f}`" for f in sorted(_available_exporters()))
    )
