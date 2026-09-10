#!/usr/bin/env bash
# One-line installer for QA Agent Pro (MCP server):
#   curl -fsSL https://raw.githubusercontent.com/OmarMokhtar-Saad/qa-agent-pro/main/install.sh | bash
#
# Safe to re-run: a complete install is UPDATED in place, an incomplete one is
# repaired, and .env / data/ / corpus/ are never touched. Needs no sudo and
# writes nothing outside $HOME. If no suitable Python is on PATH it provisions
# a private one with uv rather than asking you to find a package manager.
set -euo pipefail
REPO="OmarMokhtar-Saad/qa-agent-pro"
INSTALL_DIR="${QA_INSTALL_DIR:-$HOME/qa-agent-pro}"
# fastmcp needs 3.10+. PY_PREFERRED is the version we PROVISION when the
# machine has none -- the newest CPython line with the full wheel coverage
# this dependency set relies on, not necessarily the newest one released.
PY_PREFERRED="3.12"

die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

case "$(uname -s 2>/dev/null || echo unknown)" in
  Darwin | Linux | *BSD*) ;;
  MINGW* | MSYS* | CYGWIN*)
    die "This is the macOS/Linux/WSL installer, and you are in a Windows shell.
       Native Windows has its own one-liner -- run this in PowerShell:
         powershell -ExecutionPolicy Bypass -c \"irm https://raw.githubusercontent.com/$REPO/main/install.ps1 | iex\"" ;;
esac

command -v curl >/dev/null || die "curl is required and was not found on PATH."

py_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }

# Newest suitable interpreter already on the machine wins; the venv it creates
# is what every later start uses, so this choice is made once.
PYBIN=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then
    PYBIN="$(command -v "$c")"
    break
  fi
done

# Nothing suitable: provision one under $HOME with uv. This is deliberately not
# `brew` / `apt-get` / `dnf` -- picking the wrong one of those is the single
# most common way this install fails, and it is the only step that would want
# admin rights. uv is identical on macOS, Linux and WSL.
if [ -z "$PYBIN" ]; then
  echo "No Python 3.10+ found on PATH."
  UV=""
  if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
  elif [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"
  else
    echo "Installing uv (per-user, no sudo, into ~/.local/bin) ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 \
      || die "could not install uv. Install Python $PY_PREFERRED yourself, then re-run."
    [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
  fi
  [ -n "$UV" ] || die "uv installed but was not found at ~/.local/bin/uv."
  echo "Fetching Python $PY_PREFERRED with uv (nothing is installed system-wide) ..."
  "$UV" python install "$PY_PREFERRED" >/dev/null 2>&1 \
    || die "uv could not fetch Python $PY_PREFERRED."
  PYBIN="$("$UV" python find "$PY_PREFERRED" 2>/dev/null || true)"
  [ -n "$PYBIN" ] && [ -x "$PYBIN" ] && py_ok "$PYBIN" \
    || die "uv fetched Python $PY_PREFERRED but it could not be located."
fi
echo "Using $("$PYBIN" --version) at $PYBIN"

# An existing directory is a reason to UPDATE, not a reason to refuse. Three
# cases, and only the third ever needed you to delete anything by hand:
#   complete install -> update it in place
#   partial/leftover -> repair it by overlaying the release
#   QA_FORCE=1       -> replace the code, still keeping your data
MODE="install"
if [ -e "$INSTALL_DIR" ]; then
  [ -d "$INSTALL_DIR" ] || die "$INSTALL_DIR exists and is not a directory."
  if [ -n "${QA_FORCE:-}" ]; then
    MODE="reinstall"
  elif [ -f "$INSTALL_DIR/start.sh" ] && [ -f "$INSTALL_DIR/VERSION" ]; then
    MODE="update"
  elif [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    MODE="repair"
    echo "$INSTALL_DIR exists but is not a complete install -- repairing it."
  fi
fi

echo "Fetching the latest release of $REPO ..."
TAG=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
  | "$PYBIN" -c "import json,sys; print(json.load(sys.stdin)['tag_name'])") \
  || die "could not reach the GitHub releases API. Check your network and retry."
[ -n "$TAG" ] || die "the GitHub releases API returned no tag."

if [ "$MODE" = "update" ]; then
  HAVE="v$(tr -d ' \t\n\r' < "$INSTALL_DIR/VERSION" 2>/dev/null || true)"
  if [ "$HAVE" = "$TAG" ]; then
    echo "Already on the latest release ($TAG). Refreshing dependencies + editor registration ..."
  else
    echo "Updating $HAVE -> $TAG (your .env, suites and corpus are kept) ..."
  fi
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/release.zip" "https://github.com/$REPO/archive/refs/tags/$TAG.zip" \
  || die "could not download release $TAG."
"$PYBIN" - "$TMP" <<'PYEOF'
import sys, zipfile
from pathlib import Path
tmp = Path(sys.argv[1])
zipfile.ZipFile(tmp / "release.zip").extractall(tmp / "x")
PYEOF
SRC=$(find "$TMP/x" -mindepth 1 -maxdepth 1 -type d | head -1)
[ -n "$SRC" ] || die "release archive $TAG was empty."

mkdir -p "$INSTALL_DIR"
# Every start chmods the code read-only, so an update has to unlock before it
# can overlay. Only release files are copied: .env, data/ and corpus/ are not
# in the archive and are therefore never overwritten.
chmod -R u+w "$INSTALL_DIR" 2>/dev/null || true
cp -R "$SRC"/. "$INSTALL_DIR"/

cd "$INSTALL_DIR"
# Reuse a healthy venv (an update should not re-download every wheel); rebuild
# it when it is missing, broken, or built on a Python that is now too old.
if [ ! -x .venv/bin/python ] || ! py_ok .venv/bin/python; then
  echo "Creating virtualenv + installing dependencies (a few minutes) ..."
  rm -rf .venv
  "$PYBIN" -m venv .venv
else
  echo "Updating dependencies in the existing virtualenv ..."
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet --upgrade -e .
[ -f .env ] || cp .env.example .env
# Lock code files read-only (the launcher re-locks + self-heals each start).
.venv/bin/python -c "from pathlib import Path; from tools.updater import lock_files; lock_files(Path('.'))"
chmod +x start.sh connect.sh
echo ""
case "$MODE" in
  update)    echo "QA Agent Pro is up to date at $TAG in $INSTALL_DIR" ;;
  repair)    echo "Repaired QA Agent Pro $TAG in $INSTALL_DIR" ;;
  reinstall) echo "Reinstalled QA Agent Pro $TAG in $INSTALL_DIR" ;;
  *)         echo "Installed QA Agent Pro $TAG to $INSTALL_DIR" ;;
esac
echo ""
echo "Registering with your AI editors ..."
"$INSTALL_DIR/connect.sh" || true
echo ""
echo "Next steps:"
echo "  1. Restart Cursor / Claude, then ask it: run qa-doctor"
echo ""
echo "No API key and no login are needed - your own chat model writes the"
echo "test cases. Optional settings live in: $INSTALL_DIR/.env"
echo ""
echo "To re-register editors later, run: $INSTALL_DIR/connect.sh"
echo "To update later, just re-run this installer (or let the server self-update)."
