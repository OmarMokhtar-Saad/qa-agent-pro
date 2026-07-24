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
"$PY" - "$START" <<'PYEOF'
import json, sys
from pathlib import Path

start = sys.argv[1]
home = Path.home()


def register(path, label, requires_dir):
    if not requires_dir.is_dir():
        print(f"  - {label}: not detected — skipped")
        return
    try:
        cfg = {}
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            cfg = json.loads(text) if text else {}
            if not isinstance(cfg, dict):
                raise ValueError("config root is not a JSON object")
        servers = cfg.setdefault("mcpServers", {})
        existed = "qa-agent-pro" in servers
        servers["qa-agent-pro"] = {"command": start}
        if path.is_file():
            Path(str(path) + ".bak").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        verb = 'updated' if existed else 'added'
        print(f"  + {label}: {verb} in {path}")
    except Exception as exc:
        print(f"  ! {label}: could not update {path} ({exc}) — add manually:")
        print('      {"mcpServers": {"qa-agent-pro": {"command": "%s"}}}' % start)


register(home / ".cursor" / "mcp.json", "Cursor", home / ".cursor")
if sys.platform == "darwin":
    app = home / "Library" / "Application Support" / "Claude"
else:
    app = home / ".config" / "Claude"
register(app / "claude_desktop_config.json", "Claude Desktop", app)
PYEOF

echo "Done. Restart your editor(s) to pick up the server."
