"""Known editor config targets of tools.client_registry (split out)."""

from __future__ import annotations

import sys
from pathlib import Path

from tools.process_env import home_dir


def _claude_desktop_dir(home: Path) -> Path:
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude"
    if sys.platform == "win32":
        # %APPDATA% by its conventional path rather than the env
        # var, so a caller-supplied ``home`` still governs (tests, and
        # an install
        # driven for another profile). Cursor needs no branch at all:
        # ``~/.cursor`` is already correct on Windows.
        return home / "AppData" / "Roaming" / "Claude"
    return home / ".config" / "Claude"


# Claude Code is NOT here: it is registered through ``claude mcp add`` rather
# than by editing a file, and shelling out to a CLI does not belong in a startup
# pass. Windows IS covered (``%APPDATA%\\Claude``) as of native-Windows
# support; only the advisory lock degrades there -- see the module docstring.
def default_targets(home: Path | None = None) -> list[tuple[str, Path, Path]]:
    """(label, config_path, require_dir) for the JSON-config clients we know."""
    home = Path(home) if home is not None else home_dir()
    app = _claude_desktop_dir(home)
    return [
        ("Cursor", home / ".cursor" / "mcp.json", home / ".cursor"),
        ("Claude Desktop", app / "claude_desktop_config.json", app),
    ]


# A project-scope MCP config registers a server only while that folder is open,
# which is how a SECOND qa server appears without anyone editing a user config.
_PROJECT_CONFIG_RELPATHS = (".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json")


def atlassian_targets(home: Path | None = None) -> list[tuple[str, Path, Path]]:
    """(label, config_path, require_dir) for clients whose Atlassian entry is a FILE.

    Cursor only -- and the omissions are deliberate, not partial work:

    * **Claude Desktop** reaches Atlassian through a HOSTED Connector
      (Settings -> Connectors). There is no local entry to merge, and writing an
      ``atlassian`` object into ``claude_desktop_config.json`` would connect
      nothing while looking like it had.
    * **Claude Code** and **Gemini CLI** register through their own CLIs
      (``claude mcp add --scope user --transport http`` / ``gemini mcp add``).
      Shelling out to a CLI does not belong in this module -- the connect scripts
      do that half, and the SCOPE argument is the reason it cannot be inferred
      here: only the caller knows whether this is a user-wide install.
    """
    home = Path(home) if home is not None else home_dir()
    return [("Cursor", home / ".cursor" / "mcp.json", home / ".cursor")]
