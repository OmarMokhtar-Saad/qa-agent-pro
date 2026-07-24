#!/usr/bin/env python3
"""QA Agent Pro — MCP server launcher (distribution edition).

Configure your MCP client (Cursor / Claude Code / Claude Desktop) to run
start.sh (or `python3 launcher.py`). On every client connect, BEFORE the
server starts, this launcher automatically:

1. checks GitHub for a newer release and installs it;
2. verifies every code file against MANIFEST.sha256 and restores any
   locally-modified file from the release (self-heal);
3. re-locks all code files read-only.

These steps are mandatory in this edition and cannot be disabled from
.env. A network failure never blocks startup — the current version runs.
All launcher/updater output goes to stderr; stdout is reserved for the
MCP stdio protocol."""

import logging
import os
import subprocess
import sys

DIST_REPO = "OmarMokhtar-Saad/qa-agent-pro"


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    log = logging.getLogger("qa_agent_pro.launcher")
    try:
        from tools.updater import run_update_check

        status = run_update_check(
            force=True, repo_override=DIST_REPO, lock_override=True
        )
        log.info("Startup update check: %s", status)
    except Exception as exc:  # never block startup
        log.warning("Update check failed (%s) — starting current version.", exc)
    env = dict(os.environ)
    env["QA_MCP_ENABLED"] = "true"  # the server refuses to start without it
    return subprocess.call(
        [sys.executable, "mcp_server.py", *sys.argv[1:]],
        env=env,
        cwd=os.path.dirname(os.path.abspath(__file__)) or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
