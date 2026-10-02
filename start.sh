#!/usr/bin/env bash
# QA Agent Pro — MCP server entry point. Point your MCP client at this
# script. It runs the launcher (integrity check, then update-check +
# self-heal + read-only lock in the background) and serves MCP over stdio.
set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
exec "$PY" launcher.py "$@"
