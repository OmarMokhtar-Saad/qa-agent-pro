#!/usr/bin/env bash
# QA Agent Pro — MCP server entry point. Point your MCP client at this
# script. It runs the launcher (mandatory update-check + integrity
# self-heal + read-only lock) and then serves MCP over stdio.
set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
exec "$PY" launcher.py "$@"
