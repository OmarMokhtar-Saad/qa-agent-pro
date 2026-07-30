#!/usr/bin/env python3
"""QA Agent Pro — self-updating MCP launcher (distribution edition).

Point your MCP client (Cursor / Claude Code / Claude Desktop) at start.sh.
This launcher supervises the real MCP server as a child process and keeps
the install current WITHOUT restarting the editor:

1. On start: update check + MANIFEST.sha256 self-heal + read-only lock.
2. While running: re-checks every QA_UPDATE_INTERVAL_MINUTES (default 15).
   A newer release installs in the background; the server then restarts
   itself once no tool is running (within one check interval) and the
   recorded MCP initialize handshake is replayed — the editor keeps its
   session, nothing to do. Set QA_DRIFT_RESTART_ENABLED=false to disable.
3. If the server ever crashes it is respawned the same way.

stdout carries the MCP protocol; every log line goes to stderr. A network
failure never blocks startup — the current version keeps serving."""

import hashlib
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


def _disk_version() -> str:
    try:
        with open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"),
            encoding="utf-8",
        ) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _write_session_state() -> None:
    """Record which app version's tool schemas the client last loaded
    (observed initialize / tools-list requests). qa_setup_check compares
    this to the release that last changed the schemas and tells the user
    when a one-time editor restart is needed (editors ignore list_changed)."""
    try:
        state_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "backups"
        )
        os.makedirs(state_dir, exist_ok=True)
        with open(
            os.path.join(state_dir, "session-state.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump({"client_schema_version": _disk_version()}, fh)
    except OSError:
        log.debug("could not write session state", exc_info=True)


def _self_hash() -> str:
    try:
        with open(os.path.abspath(__file__), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


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
        self.self_hash = _self_hash()

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
        if replay and self.handshake:
            # Tool schemas may have changed across the update — tell the
            # client to re-fetch tools/list, else it keeps stale cached
            # schemas until the editor restarts.
            try:
                out = sys.stdout.buffer
                out.write(
                    b'{"jsonrpc": "2.0", '
                    b'"method": "notifications/tools/list_changed"}\n'
                )
                out.flush()
                log.info("Sent tools/list_changed to refresh client schemas.")
            except Exception:
                log.debug("could not send tools/list_changed", exc_info=True)

    def restart_child(self, reason: str) -> None:
        # A changed launcher.py hot-swaps the whole process instead.
        self.maybe_self_exec(reason)
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

    def maybe_self_exec(self, reason: str) -> None:
        """Hot-swap the launcher itself: when an update changed launcher.py
        on disk, re-exec on the new code — same PID, same stdio pipes — and
        hand the recorded MCP handshake to the next incarnation, which
        replays it and announces tools/list_changed. The editor never
        needs a restart, even for launcher changes."""
        if self.closing or _self_hash() == self.self_hash:
            return
        log.info("Launcher code changed on disk — re-exec (%s).", reason)
        with self.child_lock:
            self.restarting = True
            if self.child is not None and self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.child.kill()
            resume = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "backups",
                "handshake-resume.jsonl",
            )
            try:
                os.makedirs(os.path.dirname(resume), exist_ok=True)
                with open(resume, "wb") as fh:
                    for line in self.handshake:
                        fh.write(line)
            except OSError:
                resume = ""
            args = [sys.executable, os.path.abspath(__file__)]
            if resume and self.handshake:
                args += ["--resume", resume]
            os.execv(sys.executable, args)

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
                _write_session_state()
            elif method == "notifications/initialized" and self.handshake:
                self.handshake = self.handshake[:1] + [line]
            elif method == "tools/list":
                # The client re-fetched schemas — record the fresh version.
                _write_session_state()
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
                if status != "healed":
                    # An 'updated' status is handled by the CHILD's
                    # drift check, which can exit BETWEEN requests. This
                    # launcher cannot see in-flight work: last_activity
                    # only moves on stdio traffic, so an 8-minute
                    # generation looks idle and would be killed. A heal
                    # writes no version stamp, so the child cannot
                    # detect it -- that case stays here.
                    continue
                log.info("Integrity heal applied — restarting at the next idle minute.")
                while time.time() - self.last_activity < IDLE_SECONDS:
                    time.sleep(5)
                self.restart_child("integrity heal")
            except Exception as exc:
                log.warning("Background update check failed (%s) — will retry.", exc)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    resume_path = ""
    if "--resume" in sys.argv:
        try:
            resume_path = sys.argv[sys.argv.index("--resume") + 1]
        except IndexError:
            resume_path = ""
    if not resume_path:
        try:
            from tools.updater import run_update_check

            status = run_update_check(
                force=True, repo_override=DIST_REPO, lock_override=True
            )
            log.info("Startup update check: %s", status)
        except Exception as exc:  # never block startup
            log.warning("Update check failed (%s) — starting current version.", exc)
    sup = Supervisor()
    if resume_path:
        try:
            with open(resume_path, "rb") as fh:
                sup.handshake = [ln for ln in fh.readlines() if ln.strip()]
            os.unlink(resume_path)
        except OSError:
            sup.handshake = []
    if sup.handshake:
        log.info("Launcher self-updated — resuming the editor session.")
        sup.spawn(replay=True)
    else:
        sup.spawn(replay=False)
    threading.Thread(target=sup.watchdog, daemon=True).start()
    sup.pump_client_in()  # blocks until the editor disconnects
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
