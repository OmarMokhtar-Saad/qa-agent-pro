"""MCP server settings seam: the QA_*_ENABLED switches, read through the port.

Many modules read ``config.settings`` directly today; the readers move to
``tools.settings_port`` wave by wave, and this module re-exports the singleton
(``mcp_server`` imports ``settings`` from here) while its two switches already
read through the port.

No import-time chdir lives here (the facade ``mcp_server`` anchors the cwd before
importing this module), so every handlers module can import it standalone.
"""

from __future__ import annotations

from config.settings import settings
from tools import settings_port

__all__ = ["api_tests_enabled", "mcp_enabled", "settings"]


def api_tests_enabled() -> bool:
    """Whether the API-test edition is on (``QA_API_TEST_ENABLED``)."""
    return bool(settings_port.get().qa_api_test_enabled)


def mcp_enabled() -> bool:
    """Whether the MCP server may start (``QA_MCP_ENABLED``)."""
    return bool(settings_port.get().qa_mcp_enabled)
