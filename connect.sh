#!/usr/bin/env bash
# Register QA Agent Pro with Claude Code, Cursor, and Claude Desktop.
# Finds the install path automatically; idempotent — re-run any time:
#   ./connect.sh
set -uo pipefail
cd "$(dirname "$0")"
INSTALL_DIR="$(pwd)"
START="$INSTALL_DIR/start.sh"
PY="$INSTALL_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "QA Agent Pro — registering MCP server at: $START"

# Claude Code (CLI) — user scope so it is available in every project.
if command -v claude >/dev/null 2>&1; then
  claude mcp remove --scope user qa-agent-pro >/dev/null 2>&1 || true
  if claude mcp add --scope user qa-agent-pro -- "$START" >/dev/null 2>&1; then
    echo "  + Claude Code: registered (user scope)"
  else
    echo "  ! Claude Code: could not register automatically — run manually:"
    echo "      claude mcp add --scope user qa-agent-pro -- $START"
  fi
else
  echo "  - Claude Code: 'claude' CLI not found — skipped"
fi

# Cursor + Claude Desktop — merge JSON configs, preserving other servers.
# Delegates to tools/client_registry so this script and the launcher's
# startup pass share ONE implementation. This caller runs in REPAIR mode
# (insert_only=False) on purpose: re-running connect.sh after the install
# moves must fix a stale command, while the startup pass must never rewrite
# an entry the tester edited by hand.
"$PY" - "$START" <<'PYEOF'
import sys

from tools.client_registry import register_all

for label, status, detail in register_all(sys.argv[1], insert_only=False):
    if status == "skipped":
        print(f"  - {label}: not detected — skipped")
    elif status == "error":
        print(f"  ! {label}: could not update ({detail}) — add manually:")
        print(
            '      {"mcpServers": {"qa-agent-pro": {"command": "%s"}}}'
            % sys.argv[1]
        )
    else:
        print(f"  + {label}: {status} ({detail})")
PYEOF

echo "Done. Restart your editor(s) to pick up the server."
