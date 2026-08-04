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

# ---- Atlassian (Jira Cloud) ---------------------------------------------
# Jira is read through the TESTER'S OWN Atlassian MCP connection, so the
# entry has to exist in THEIR client config -- and hand-editing mcpServers
# JSON is exactly where a non-technical tester stops. Writing it is NOT
# authorizing it: OAuth is still one click inside the editor, so every line
# below says so. QA_REGISTER_ATLASSIAN_MCP=false skips the whole block.
#
# Three exit states, deliberately distinguished: 0 = flag on, 3 = flag off,
# anything else = the probe itself could not run (no venv, no pydantic).
#
# Flag-off is 3, NOT 1, and that is load-bearing: an uncaught ImportError
# exits 1, so `else 1` would have reported a broken venv -- the single most
# likely failure here -- as 'you turned this off', sending the tester to edit
# a .env value that was already correct. Verified: python -c 'import missing'
# exits 1. The
# third must NOT be reported as 'you turned this off' -- that sends a tester
# to edit a .env value that is already correct. All non-zero paths print the
# JSON to add by hand, so no branch is a dead end and none writes blindly.
ATLAS_URL="https://mcp.atlassian.com/v1/mcp/authv2"
"$PY" -c "from config.settings import settings; raise SystemExit(0 if settings.qa_register_atlassian_mcp else 3)" >/dev/null 2>&1
ATLAS_RC=$?
if [ "$ATLAS_RC" -eq 0 ]; then
  echo ""
  echo "Connecting Atlassian (Jira Cloud) — one OAuth click is still yours:"
  if command -v claude >/dev/null 2>&1; then
    # --scope user, exactly like the qa-agent-pro registration above: this
    # script runs with cwd=$INSTALL_DIR, so a default (local) scope would
    # bind the entry to the INSTALL directory and it would be invisible in
    # the tester's real project -- after we printed 'added'. No --scope on
    # `get`: it looks across scopes, and this is not the place to assume a
    # flag that has not been verified against the installed CLI.
    if claude mcp get atlassian >/dev/null 2>&1; then
      echo "  - Claude Code: an atlassian entry already exists (user or"
      echo "    project scope) — left alone"
    elif claude mcp add --scope user --transport http atlassian "$ATLAS_URL" >/dev/null 2>&1; then
      echo "  + Claude Code: added — run /mcp and authenticate atlassian"
    else
      echo "  ! Claude Code: could not add it — run manually:"
      echo "      claude mcp add --scope user --transport http atlassian $ATLAS_URL"
    fi
  fi
  "$PY" - <<'PYEOF2'
from tools.client_registry import register_atlassian

for label, status, detail in register_atlassian():
    if status == "skipped":
        print(f"  - {label}: not detected — skipped")
    elif status == "present":
        print(f"  - {label}: an atlassian entry already exists — left alone")
    elif status == "error":
        print(f"  ! {label}: could not update ({detail}) — add manually:")
        print(
            '      {"mcpServers": {"atlassian": {"type": "http", '
            '"url": "https://mcp.atlassian.com/v1/mcp/authv2"}}}'
        )
    else:
        print(f"  + {label}: {status} ({detail}) — restart it to finish OAuth")
PYEOF2
  echo "  - Claude Desktop: claude.ai → Settings → Connectors → Atlassian → Connect"
elif [ "$ATLAS_RC" -eq 3 ]; then
  echo ""
  echo "Atlassian auto-connect is off (QA_REGISTER_ATLASSIAN_MCP=false)."
  echo "  To read Jira tickets, add this under mcpServers yourself and restart:"
  echo "      \"atlassian\": {\"type\": \"http\", \"url\": \"$ATLAS_URL\"}"
else
  echo ""
  echo "Could not read your settings, so Atlassian was left alone (nothing"
  echo "  was written). This is NOT a flag you set. To read Jira tickets, add"
  echo "  this under mcpServers yourself and restart your editor:"
  echo "      \"atlassian\": {\"type\": \"http\", \"url\": \"$ATLAS_URL\"}"
fi

echo "Done. Restart your editor(s) to pick up the server."
