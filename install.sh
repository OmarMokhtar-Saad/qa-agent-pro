#!/usr/bin/env bash
# One-line installer for QA Agent Pro (MCP server):
#   curl -fsSL https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.sh | bash
set -euo pipefail
REPO="OmarMokhtar-Saad/qa-agent-pro"
INSTALL_DIR="${QA_INSTALL_DIR:-$HOME/qa-agent-pro}"

command -v curl >/dev/null || { echo "ERROR: curl is required"; exit 1; }

# fastmcp needs Python 3.10+; macOS system python3 is often 3.9. Pick the
# newest suitable interpreter (the venv it creates is used from then on).
PYBIN=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 \
     && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYBIN="$c"
    break
  fi
done
[ -n "$PYBIN" ] || {
  echo "ERROR: Python 3.10 or newer is required (none found on PATH)."
  echo "Install it with:  brew install python@3.12   (or from python.org)"
  exit 1
}
echo "Using $($PYBIN --version) at $(command -v $PYBIN)"

if [ -e "$INSTALL_DIR" ] && [ -z "${QA_FORCE:-}" ]; then
  echo "$INSTALL_DIR already exists."
  echo "Updates are automatic every time your MCP client starts the server."
  echo "Set QA_FORCE=1 to reinstall from scratch."
  exit 1
fi

echo "Fetching the latest release of $REPO ..."
TAG=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
  | "$PYBIN" -c "import json,sys; print(json.load(sys.stdin)['tag_name'])")
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/release.zip" "https://github.com/$REPO/archive/refs/tags/$TAG.zip"
"$PYBIN" - "$TMP" <<'PYEOF'
import sys, zipfile
from pathlib import Path
tmp = Path(sys.argv[1])
zipfile.ZipFile(tmp / "release.zip").extractall(tmp / "x")
PYEOF
SRC=$(find "$TMP/x" -mindepth 1 -maxdepth 1 -type d | head -1)
mkdir -p "$INSTALL_DIR"
cp -R "$SRC"/. "$INSTALL_DIR"/

cd "$INSTALL_DIR"
echo "Creating virtualenv + installing dependencies (a few minutes) ..."
"$PYBIN" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .
[ -f .env ] || cp .env.example .env
# Lock code files read-only (the launcher re-locks + self-heals each start).
.venv/bin/python -c "from pathlib import Path; from tools.updater import lock_files; lock_files(Path('.'))"
chmod +x start.sh connect.sh
echo ""
echo "Installed QA Agent Pro $TAG to $INSTALL_DIR"
echo ""
echo "Registering with your AI editors ..."
"$INSTALL_DIR/connect.sh" || true
echo ""
echo "Next steps:"
echo "  1. Restart Cursor / Claude, then ask it: run qa_setup_check"
echo ""
echo "No API key and no login are needed - your own chat model writes the"
echo "test cases. Optional settings live in: $INSTALL_DIR/.env"
echo ""
echo "To re-register editors later, run: $INSTALL_DIR/connect.sh"
