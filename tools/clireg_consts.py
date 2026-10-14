"""Constants of tools.client_registry (split out of client_registry.py)."""

from __future__ import annotations

SERVER_NAME = "qa-agent-pro"

# The HOSTED Atlassian MCP server (Jira Cloud, OAuth 2.1). Jira is read through
# the tester's OWN connection, so this entry has to exist in THEIR client config
# -- and writing it is the difference between "paste a ticket URL" and "hand-edit
# a JSON file", which is where non-technical testers stop.
#
# It only CONFIGURES the server. The OAuth click still happens inside the editor,
# and no caller may report Jira as connected on the strength of this write.
#
# The same URL appears in tools/jira_mcp.py's instruction text. It is duplicated
# on purpose: this module imports no project module by house rule (see the module
# docstring), so the two are kept in step by hand.
ATLASSIAN_NAME = "atlassian"
ATLASSIAN_ENTRY = {"type": "http", "url": "https://mcp.atlassian.com/v1/mcp/authv2"}

# Status strings, so callers can report without re-deriving intent.
ADDED = "added"
UPDATED = "updated"
PRESENT = "present"
SKIPPED = "skipped"
ERROR = "error"


# What the "never raises" helpers below can actually meet: file-system and JSON
# errors, malformed config shapes, and ``Path.resolve`` symlink loops.
_RECOVERABLE = (
    OSError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    RuntimeError,
)
