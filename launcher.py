#!/usr/bin/env python3
"""QA Agent Pro — self-updating MCP launcher (distribution edition).

Point your MCP client (Cursor / Claude Code / Claude Desktop) at start.sh.
This launcher supervises the real MCP server as a child process and keeps
the install current WITHOUT restarting the editor:

1. On start: update check + MANIFEST.sha256 self-heal + read-only lock.
2. While running: re-checks every QA_UPDATE_INTERVAL_MINUTES (default 15).
   A newer release installs in the background; at the next idle minute the
   child server restarts on the new code and the recorded MCP initialize
   handshake is replayed — the editor keeps its session, nothing to do.
3. If the server ever crashes it is respawned the same way.

stdout carries the MCP protocol; every log line goes to stderr. A network
failure never blocks startup — the current version keeps serving."""

import json
import logging
import os
import subprocess
import sys
import threading
import time

DIST_REPO = "OmarMokhtar-Saad/qa-agent-pro"
IDLE_SECONDS = 60

log = logging.getLogger("qa_agent_pro.launcher")


def _interval_seconds() -> float:
    try:
        minutes = float(os.environ.get("QA_UPDATE_INTERVAL_MINUTES", "15"))
    except ValueError:
        minutes = 15.0
    return max(60.0, minutes * 60.0)


class Supervisor:
    """Owns the editor's stdio; proxies to a restartable child MCP server."""

    def __init__(self) -> None:
        self.child = None
        self.child_lock = threading.RLock()
        self.handshake = []  # client's initialize + initialized lines
        self.swallow_id = None  # drop the child's reply to a REPLAYED initialize
        self.last_activity = time.time()
        self.closing = False
        self.restarting = False

    # ------------------------------------------------- child lifecycle
    def spawn(self, replay: bool) -> None:
        env = dict(os.environ)
        env["QA_MCP_ENABLED"] = "true"  # the server refuses to start without it
        with self.child_lock:
            self.child = subprocess.Popen(
                [sys.executable, "mcp_server.py"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                env=env,
                cwd=os.path.dirname(os.path.abspath(__file__)) or None,
            )
            if replay and self.handshake:
                try:
                    self.swallow_id = json.loads(self.handshake[0]).get("id")
                except ValueError:
                    self.swallow_id = None
                for line in self.handshake:
                    self.child.stdin.write(line)
                self.child.stdin.flush()
        threading.Thread(
            target=self._pump_child_out, args=(self.child,), daemon=True
        ).start()

    def restart_child(self, reason: str) -> None:
        with self.child_lock:
            self.restarting = True
            old = self.child
            if old is not None and old.poll() is None:
                old.terminate()
                try:
                    old.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    old.kill()
            log.info("Restarting MCP server (%s).", reason)
            self.spawn(replay=True)
            self.restarting = False

    # ------------------------------------------------------------ pumps
    def _pump_child_out(self, child) -> None:
        out = sys.stdout.buffer
        for line in iter(child.stdout.readline, b""):
            self.last_activity = time.time()
            if self.swallow_id is not None:
                try:
                    if json.loads(line).get("id") == self.swallow_id:
                        self.swallow_id = None
                        continue  # reply to the replayed initialize
                except ValueError:
                    pass
            out.write(line)
            out.flush()
        # EOF — the child died. Unless this launcher killed it on purpose,
        # bring it back and replay the handshake (crash resilience).
        if self.closing or self.restarting or child is not self.child:
            return
        log.warning("MCP server exited unexpectedly — respawning.")
        self.restart_child("crash recovery")

    def pump_client_in(self) -> None:
        stdin = sys.stdin.buffer
        for line in iter(stdin.readline, b""):
            self.last_activity = time.time()
            try:
                method = json.loads(line).get("method")
            except ValueError:
                method = None
            if method == "initialize":
                self.handshake = [line]
            elif method == "notifications/initialized" and self.handshake:
                self.handshake = self.handshake[:1] + [line]
            for _attempt in range(3):
                with self.child_lock:
                    child = self.child
                try:
                    child.stdin.write(line)
                    child.stdin.flush()
                    break
                except Exception:
                    time.sleep(1)  # child mid-restart — retry on the new one
        # The editor closed our stdin: shut everything down.
        self.closing = True
        with self.child_lock:
            if self.child is not None and self.child.poll() is None:
                self.child.terminate()

    # -------------------------------------------------- update watchdog
    def watchdog(self) -> None:
        from tools.updater import run_update_check

        interval = _interval_seconds()
        while not self.closing:
            time.sleep(interval)
            try:
                status = run_update_check(
                    force=True, repo_override=DIST_REPO, lock_override=True
                )
                if status not in ("updated", "healed"):
                    continue
                log.info("New release installed — applying at the next idle minute.")
                while time.time() - self.last_activity < IDLE_SECONDS:
                    time.sleep(5)
                self.restart_child("update installed")
            except Exception as exc:
                log.warning("Background update check failed (%s) — will retry.", exc)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        from tools.updater import run_update_check

        status = run_update_check(
            force=True, repo_override=DIST_REPO, lock_override=True
        )
        log.info("Startup update check: %s", status)
    except Exception as exc:  # never block startup
        log.warning("Update check failed (%s) — starting current version.", exc)
    sup = Supervisor()
    sup.spawn(replay=False)
    threading.Thread(target=sup.watchdog, daemon=True).start()
    sup.pump_client_in()  # blocks until the editor disconnects
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
