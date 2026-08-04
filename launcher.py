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
4. qa_setup_check is retried across such a reload automatically: the
   client makes ONE call and gets the final, post-reload report.

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


SETUP_CHECK_TOOL = "qa_setup_check"
SETUP_CHECK_MAX_RETRIES = 2
SETUP_CHECK_TIMEOUT_S = 25.0
# Fragments of the child's 'a reload was scheduled' reply
# (tools/mcp_handlers._reloading_message). Two ASCII-only pieces on
# purpose: the real sentence joins them with an em dash, which JSON
# encoders emit as an escape sequence, so a single literal spanning
# it would silently stop matching.
RELOAD_MARKERS = (
    "This takes about 10 seconds",
    "qa_setup_check` again",
)


def _as_dict(line):
    """Parse a JSON-RPC line; {} for anything that is not an object."""
    try:
        msg = json.loads(line)
    except ValueError:
        return {}
    return msg if isinstance(msg, dict) else {}


def _result_text(msg) -> str:
    """Every string inside a JSON-RPC result, joined. Shape-agnostic on
    purpose: the tool-result framing (content blocks, structuredContent)
    belongs to the child MCP library, not to this launcher."""
    chunks = []

    def walk(node):
        if isinstance(node, str):
            chunks.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(msg.get("result"))
    return " ".join(chunks)


def _is_reload_pending(msg) -> bool:
    """True for the placeholder the child returns when it has just
    scheduled a reload; false for a real setup report or an error.
    """
    if not isinstance(msg, dict) or "result" not in msg:
        return False
    text = _result_text(msg)
    return all(marker in text for marker in RELOAD_MARKERS)


def _tool_call_name(msg) -> str:
    """Tool name of a tools/call request, else an empty string."""
    if not isinstance(msg, dict) or msg.get("method") != "tools/call":
        return ""
    params = msg.get("params")
    if not isinstance(params, dict):
        return ""
    name = params.get("name")
    return name if isinstance(name, str) else ""


class Supervisor:
    """Owns the editor's stdio; proxies to a restartable child MCP server."""

    def __init__(self) -> None:
        self.child = None
        self.child_started_at = 0.0  # wall-clock at last spawn() -- see watchdog()
        self.child_lock = threading.RLock()
        self.handshake = []  # client's initialize + initialized lines
        self.swallow_id = None  # drop the child's reply to a REPLAYED initialize
        self.last_activity = time.time()
        self.closing = False
        self.restarting = False
        self.self_hash = _self_hash()
        # qa_setup_check reload auto-retry (see maybe_hold_setup_check)
        self.setup_lock = threading.RLock()
        self.setup_id = None  # id of the in-flight qa_setup_check call
        self.setup_request = None  # its raw request line, for the re-send
        self.setup_held = None  # its withheld 'reloading now' reply
        self.setup_retries = 0
        self.setup_deadline = 0.0
        # Serialises EVERY writer to the client stdio: the output pump,
        # spawn()'s tools/list_changed notice and release_setup_check()'s
        # timeout thread. A JSON-RPC line above PIPE_BUF is NOT an atomic
        # pipe write, so unsynchronised writers could interleave and
        # corrupt every in-flight call sharing this proxy. Reentrant, and
        # always the innermost lock -- nothing else is acquired under it.
        self.stdout_lock = threading.RLock()

    # ------------------------------------------------- child lifecycle
    def spawn(self, replay: bool) -> None:
        env = dict(os.environ)
        env["QA_MCP_ENABLED"] = "true"  # the server refuses to start without it
        with self.child_lock:
            self.child_started_at = time.time()
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
                notice = (
                    b'{"jsonrpc": "2.0", '
                    b'"method": "notifications/tools/list_changed"}\n'
                )
                with self.stdout_lock:
                    out.write(notice)
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
            if self.maybe_hold_setup_check(line):
                continue  # answered after the reload, in this same call
            with self.stdout_lock:
                out.write(line)
                out.flush()
        # EOF — the child died. Unless this launcher killed it on purpose,
        # bring it back and replay the handshake (crash resilience).
        if self.closing or self.restarting or child is not self.child:
            return
        log.warning("MCP server exited unexpectedly — respawning.")
        self.restart_child("crash recovery")
        self.resend_setup_check()

    def pump_client_in(self) -> None:
        stdin = sys.stdin.buffer
        for line in iter(stdin.readline, b""):
            self.last_activity = time.time()
            msg = _as_dict(line)
            method = msg.get("method")
            if method == "initialize":
                self.handshake = [line]
                _write_session_state()
            elif method == "notifications/initialized" and self.handshake:
                self.handshake = self.handshake[:1] + [line]
            elif method == "tools/list":
                # The client re-fetched schemas — record the fresh version.
                _write_session_state()
            if _tool_call_name(msg) == SETUP_CHECK_TOOL:
                self.note_setup_check(msg.get("id"), line)
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

    # ------------------------------ qa_setup_check reload auto-retry
    def note_setup_check(self, request_id, line) -> None:
        """Remember an in-flight qa_setup_check call so its reply can be
        intercepted. Only the newest is tracked: every call carries its own
        id and the client matches replies itself, so a second call simply
        supersedes the first."""
        with self.setup_lock:
            self._reset_setup_check()
            self.setup_id = request_id
            self.setup_request = line

    def _reset_setup_check(self) -> None:
        self.setup_id = None
        self.setup_request = None
        self.setup_held = None
        self.setup_retries = 0
        self.setup_deadline = 0.0

    def maybe_hold_setup_check(self, line) -> bool:
        """True when *line* is the tracked qa_setup_check reply saying a
        reload was just scheduled. The caller must NOT forward it: the child
        exits seconds later, the EOF path respawns it and re-sends the same
        request, so the client makes ONE call and still gets the final,
        post-reload report. Every other line is left untouched."""
        with self.setup_lock:
            if self.setup_id is None:
                return False
            msg = _as_dict(line)
            if not msg or msg.get("id") != self.setup_id:
                return False
            if not _is_reload_pending(msg):
                self._reset_setup_check()  # the real report -- forward it
                return False
            if self.setup_retries >= SETUP_CHECK_MAX_RETRIES:
                log.warning(
                    "qa_setup_check still reloading after %d retries.",
                    self.setup_retries,
                )
                # Forward the notice; the tester can still retry by hand.
                self._reset_setup_check()
                return False
            self.setup_held = line
            if self.setup_deadline == 0.0:
                self.setup_deadline = time.time() + SETUP_CHECK_TIMEOUT_S
                threading.Thread(
                    target=self._setup_check_guard, daemon=True
                ).start()
            return True

    def resend_setup_check(self) -> None:
        """After a respawn: re-issue the held qa_setup_check to the new child
        under the ORIGINAL id, because that is the id the client is waiting
        on."""
        with self.setup_lock:
            if self.setup_held is None or self.setup_request is None:
                return
            self.setup_retries += 1
            line = self.setup_request
        with self.child_lock:
            child = self.child
        try:
            child.stdin.write(line)
            child.stdin.flush()
            log.info("Re-sent qa_setup_check to the reloaded server.")
        except Exception:
            log.warning(
                "Could not re-send qa_setup_check -- releasing the held reply."
            )
            self.release_setup_check()

    def release_setup_check(self) -> None:
        """Hand the client the held reply after all: the fallback whenever the
        invisible retry cannot finish, so the call is never left hanging and
        the tester still sees the run-it-again notice."""
        with self.setup_lock:
            line = self.setup_held
            self._reset_setup_check()
        if line is None:
            return
        try:
            out = sys.stdout.buffer
            with self.stdout_lock:
                out.write(line)
                out.flush()
        except Exception:
            # Never silent: a lost release means the client is still
            # waiting on an id nobody will answer.
            log.warning(
                "Could not release the held qa_setup_check reply.",
                exc_info=True,
            )

    def _setup_check_guard(self) -> None:
        """Bound the invisible retry: a client tool call must never hang on us.
        Releases the held reply when the successor has not answered within
        SETUP_CHECK_TIMEOUT_S (child exit + respawn + handshake + re-call)."""
        while True:
            with self.setup_lock:
                deadline = self.setup_deadline
                if self.setup_held is None or deadline == 0.0:
                    return  # resolved normally
            if self.closing:
                return
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(0.25, remaining))
        log.warning(
            "qa_setup_check auto-retry timed out -- returning the notice."
        )
        self.release_setup_check()

    # -------------------------------------------------- update watchdog
    def _env_changed_since_child_start(self) -> bool:
        """True when .env was written after the CURRENT child started.

        Mirrors tools.mcp_handlers._env_changed_since_start, but compares
        against child_started_at (this Supervisor's own record of when its
        child last spawned) rather than the child's in-process import time --
        the two are effectively the same instant, and this avoids importing
        the full app into the long-lived Supervisor just to read one field.
        Never raises: an unreadable .env reads as unchanged, so a failure
        here can only ever SKIP a restart, never trigger a spurious one."""
        try:
            env_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), ".env"
            )
            return (
                os.path.isfile(env_path)
                and os.path.getmtime(env_path) > self.child_started_at
            )
        except OSError:
            log.debug("could not stat .env for the watchdog drift check", exc_info=True)
            return False

    def watchdog(self) -> None:
        from tools.updater import run_update_check

        interval = _interval_seconds()
        while not self.closing:
            time.sleep(interval)
            try:
                status = run_update_check(
                    force=True, repo_override=DIST_REPO, lock_override=True
                )
                if status != "healed" and not self._env_changed_since_child_start():
                    # An 'updated' status is handled by the CHILD's
                    # drift check, which can exit BETWEEN requests. This
                    # launcher cannot see in-flight work: last_activity
                    # only moves on stdio traffic, so an 8-minute
                    # generation looks idle and would be killed. A heal
                    # writes no version stamp, so the child cannot
                    # detect it -- that case stays here.
                    continue
                reason = (
                    "integrity heal"
                    if status == "healed"
                    else "config (.env changed)"
                )
                log.info("%s applied — restarting at the next idle minute.", reason)
                while time.time() - self.last_activity < IDLE_SECONDS:
                    time.sleep(5)
                self.restart_child(reason)
            except Exception as exc:
                log.warning("Background update check failed (%s) — will retry.", exc)


def main() -> int:
    # stderr is rendered as '[error] ...' by MCP clients (Cursor showed
    # every updater INFO line as an error on the v1.38.0 validation run),
    # so stderr carries WARNING+ only and the INFO trail goes to
    # data/logs/launcher.log. The threshold must sit on the HANDLER: a
    # root-level threshold alone re-leaks INFO the moment the root drops
    # to INFO for the file handler.
    _stderr = logging.StreamHandler(sys.stderr)
    _stderr.setLevel(logging.WARNING)
    _stderr.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    _root = logging.getLogger()
    _root.setLevel(logging.INFO)
    _root.addHandler(_stderr)
    try:
        from logging.handlers import RotatingFileHandler

        _log_dir = INSTALL_DIR / "data" / "logs"
        _log_dir.mkdir(parents=True, exist_ok=True)
        _fh = RotatingFileHandler(
            str(_log_dir / "launcher.log"),
            maxBytes=1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        _fh.setLevel(logging.INFO)
        _fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        _root.addHandler(_fh)
    except Exception:  # a log-file failure must never block startup
        _stderr.setLevel(logging.INFO)
    for _noisy in ("httpx", "httpcore"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)
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
        # Fix 7: pick up an editor installed AFTER this server was. A
        # SEPARATE step from the update check on purpose -- code integrity
        # and editor registration must not gate one another through
        # QA_AUTO_UPDATE_ENABLED. insert_only, so an entry a tester edited
        # by hand is never rewritten. Never blocks startup.
        try:
            from config.settings import settings as _s

            if bool(getattr(_s, "qa_auto_register_clients", False)):
                from tools.client_registry import register_all

                _start = str(INSTALL_DIR / "start.sh")
                for _label, _status, _detail in register_all(
                    _start, insert_only=True
                ):
                    if _status in ("added", "error"):
                        log.info("MCP registration: %s: %s (%s)", _label, _status, _detail)
        except Exception as exc:  # never block startup
            log.warning("Client registration pass failed (%s).", exc)
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
