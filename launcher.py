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
4. qa-doctor is retried across such a reload automatically: the
   client makes ONE call and gets the final, post-reload report.

stdout carries the MCP protocol; every log line goes to stderr. A network
failure never blocks startup — the current version keeps serving."""

import hashlib
import json
import logging
import os
import random
import subprocess
import sys
import threading
import time

DIST_REPO = "OmarMokhtar-Saad/qa-agent-pro"
IDLE_SECONDS = 60
# The idle wait is UNBOUNDED on purpose (2026-08-09 review): a cap that
# FORCED the restart would terminate() the child, and the child's
# in-flight drain (mcp_server._INFLIGHT / _DRAIN_IDLE_S) does NOT apply
# to a launcher SIGTERM -- so a forced restart could kill a running
# generation. A long deferral is made visible by a periodic WARNING
# instead. Trade-off: a client that polls faster than IDLE_SECONDS can
# defer a .env change indefinitely; the WARNING says exactly that.
IDLE_WARN_EVERY_S = 300
# mcp_server.DRIFT_RESTART_EXIT_CODE -- the child's DELIBERATE 'a peer
# client installed a new version, reload me' exit. Matched in
# _pump_child_out so a reload is never reported to the editor as a
# crash (2026-08-09). The two literals MUST stay equal;
# tests/test_launcher_drift_exit.py asserts it.
DRIFT_EXIT_CODE = 86
# Several MCP clients share ONE install, so a single release makes every
# supervisor respawn within the same second: JITTER is what de-syncs
# them. The crash backoff on top is deliberately TINY -- pump_client_in
# only retries a write to a missing child for CHILD_WRITE_RETRIES x
# CHILD_WRITE_RETRY_SLEEP_S seconds, and a respawn slower than that
# budget would DROP a client request and hang the editor on its id.
RESPAWN_JITTER_S = 1.0
RESPAWN_BACKOFF_BASE_S = 0.5
RESPAWN_BACKOFF_MAX_S = 1.0
RESPAWN_HEALTHY_S = 60.0
# How long the EOF path waits for the child's exit status. poll() alone
# RACES the reap and can still return None, which mis-classified a
# DELIBERATE drift exit as a crash (2026-08-09, review M5). This wait is
# spent on the pump thread BEFORE the respawn, so it is part of the
# budget below and is deliberately small;
# tests/test_launcher_drift_exit.py asserts
# CHILD_REAP_WAIT_S + RESPAWN_BACKOFF_MAX_S + RESPAWN_JITTER_S stays
# inside the client write-retry budget.
CHILD_REAP_WAIT_S = 1.0
# Write-retry budget for a client line that arrives mid-restart. MUST
# outlast CHILD_REAP_WAIT_S + RESPAWN_BACKOFF_MAX_S + RESPAWN_JITTER_S.
CHILD_WRITE_RETRIES = 8
CHILD_WRITE_RETRY_SLEEP_S = 1.0

# Used by the log-file dir below and the client-registration pass.
# Was referenced without being defined until 2026-08-04: both call
# sites are inside never-raise try blocks, so the NameError silently
# degraded launcher logging to INFO-on-stderr on every start and made
# QA_AUTO_REGISTER_CLIENTS a no-op.
from pathlib import Path as _PathLib

INSTALL_DIR = _PathLib(os.path.dirname(os.path.abspath(__file__)))

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
    (observed initialize / tools-list requests). qa-doctor compares
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


def _env_semantic_lines(text):
    """Setting lines of a .env: blank lines, full-line comments and
    surrounding whitespace removed."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _env_fingerprint():
    """sha256 of the install's .env SEMANTIC content ("" when the file is
    absent or unreadable).

    CONTENT, not mtime (2026-08-09). Two things rewrite a running
    install's .env: updater.migrate_env(), which appends a dated banner of
    newly shipped keys after an update, and env_heal.heal_env(), which
    qa-doctor runs. Under the mtime check EITHER rewrite looked like a
    tester edit -- so a release fanned a restart out across every
    supervisor sharing this install, even when the settings were
    unchanged. Mirrors the content-based rule
    mcp_handlers._code_changed_since_start already applies to VERSION.
    Never raises."""
    try:
        env_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".env"
        )
        if not os.path.isfile(env_path):
            return ""
        digest = hashlib.sha256()
        with open(env_path, "r", encoding="utf-8", errors="replace") as fh:
            # Capped: a .env is a few KiB, and a supervisor must never
            # slurp an arbitrarily large file on a 15-minute timer.
            for line in _env_semantic_lines(fh.read(1024 * 1024)):
                digest.update(line.encode("utf-8"))
                digest.update(b"|")
        return digest.hexdigest()
    except Exception:
        log.debug("could not fingerprint .env for the watchdog", exc_info=True)
        return ""


def _self_hash() -> str:
    try:
        with open(os.path.abspath(__file__), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


SETUP_CHECK_TOOL = "qa-doctor"
SETUP_CHECK_MAX_RETRIES = 2
SETUP_CHECK_TIMEOUT_S = 25.0
# Fragments of the child's 'a reload was scheduled' reply
# (tools/mcp_handlers._reloading_message). Two ASCII-only pieces on
# purpose: the real sentence joins them with an em dash, which JSON
# encoders emit as an escape sequence, so a single literal spanning
# it would silently stop matching.
RELOAD_MARKERS = (
    "This takes about 10 seconds",
    "qa-doctor` again",
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
        # Semantic hash of the .env the CURRENT child was started on, and
        # the consecutive-crash counter behind the respawn backoff.
        self.env_fingerprint = _env_fingerprint()
        self.respawn_failures = 0
        self.child_lock = threading.RLock()
        self.handshake = []  # client's initialize + initialized lines
        self.swallow_id = None  # drop the child's reply to a REPLAYED initialize
        self.last_activity = time.time()
        self.closing = False
        self.restarting = False
        self.self_hash = _self_hash()
        # qa-doctor reload auto-retry (see maybe_hold_setup_check)
        self.setup_lock = threading.RLock()
        self.setup_id = None  # id of the in-flight qa-doctor call
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
            # Re-stamped on EVERY start: applying a .env change clears it,
            # so one rewrite costs at most one restart per supervisor.
            self.env_fingerprint = _env_fingerprint()
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
        if sys.platform == "win32":
            # os.execv does NOT replace the process on Windows: it starts a
            # NEW pid and this one exits, so the editor sees the child it
            # spawned die and drops the session. Losing a tester's session
            # is worse than a delayed launcher update, so the new code
            # applies on the next start instead -- the README's documented
            # 'one editor restart' case. The SERVER half still updates
            # live: restart_child() respawns via subprocess, which is fine
            # here. Stamping the hash keeps this to one log line, not one
            # per 15-minute update tick.
            self.self_hash = _self_hash()
            log.info(
                "Launcher code changed on disk; it applies on the next "
                "start (Windows cannot re-exec in place)."
            )
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
        try:
            # 2026-08-09 (review M5): poll() right after stdout EOF
            # RACES the reap and can still return None, so a deliberate
            # drift exit (86) was intermittently classified as a crash
            # and logged at WARNING -- i.e. stderr, i.e. an error badge
            # in the editor, which is the exact regression the drift
            # exit code exists to prevent. wait() blocks briefly for the
            # status the child is about to have; the bound is part of
            # the respawn budget (see CHILD_REAP_WAIT_S).
            code = child.wait(timeout=CHILD_REAP_WAIT_S)
        except Exception:
            # Includes TimeoutExpired: fall back to the old read rather
            # than block the supervisor's respawn any longer.
            try:
                code = child.poll()
            except Exception:
                code = None
        deliberate = code == DRIFT_EXIT_CODE
        if deliberate:
            # NOT a crash: mcp_server._drift_watch exits with exactly this
            # code, once no tool is running, to load a version a PEER client
            # installed. Reporting it at WARNING put it on stderr, which MCP
            # clients render as an error -- so every release showed the
            # tester 'MCP server exited unexpectedly', once per connected
            # client (observed 2026-08-09).
            self.respawn_failures = 0
            log.info(
                "MCP server exited to load a newly installed version "
                "(exit %s) — restarting it.",
                code,
            )
            reason = "version drift"
        else:
            if time.time() - self.child_started_at >= RESPAWN_HEALTHY_S:
                self.respawn_failures = 0  # it had been serving fine
            self.respawn_failures += 1
            log.warning(
                "MCP server exited unexpectedly (exit %s) — respawning.", code
            )
            reason = "crash recovery"
        delay = self._respawn_delay(deliberate)
        if delay:
            time.sleep(delay)
        if self.closing:
            return
        self.restart_child(reason)
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
            for _attempt in range(CHILD_WRITE_RETRIES):
                with self.child_lock:
                    child = self.child
                try:
                    child.stdin.write(line)
                    child.stdin.flush()
                    break
                except Exception:
                    # Child mid-restart -- retry on the new one. This budget
                    # MUST outlast the worst-case _respawn_delay, or the line
                    # is dropped and the editor waits forever on that id.
                    time.sleep(CHILD_WRITE_RETRY_SLEEP_S)
            else:
                # Never silent: a dropped request is an editor hang.
                log.warning(
                    "Dropped a client request: the server did not come back "
                    "within %d attempts.",
                    CHILD_WRITE_RETRIES,
                )
        # The editor closed our stdin: shut everything down.
        self.closing = True
        with self.child_lock:
            if self.child is not None and self.child.poll() is None:
                self.child.terminate()

    # ------------------------------ qa-doctor reload auto-retry
    def note_setup_check(self, request_id, line) -> None:
        """Remember an in-flight qa-doctor call so its reply can be
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
        """True when *line* is the tracked qa-doctor reply saying a
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
                    "qa-doctor still reloading after %d retries.",
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
        """After a respawn: re-issue the held qa-doctor to the new child
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
            log.info("Re-sent qa-doctor to the reloaded server.")
        except Exception:
            log.warning(
                "Could not re-send qa-doctor -- releasing the held reply."
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
                "Could not release the held qa-doctor reply.",
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
            "qa-doctor auto-retry timed out -- returning the notice."
        )
        self.release_setup_check()

    # -------------------------------------------------- update watchdog
    def _env_changed_since_child_start(self) -> bool:
        """True when .env's SEMANTIC content differs from the content the
        CURRENT child was started on.

        Compares a content hash against self.env_fingerprint, which spawn()
        re-stamps on every start. mtime was the 2026-08-09 defect: the
        launcher's OWN run_update_check() calls migrate_env(), which
        rewrites .env, so a release made every supervisor sharing this
        install restart on the next tick even when no setting had changed.
        A hash makes a no-op rewrite free.

        Never raises, and an unreadable .env fingerprints as empty and reads
        as UNCHANGED, so a failure here can only ever SKIP a restart, never
        trigger a spurious one."""
        current = _env_fingerprint()
        if not current:
            return False
        # An empty baseline (no readable .env at spawn) plus a readable one
        # now IS a change -- and the restart re-stamps it, so it cannot loop.
        return current != self.env_fingerprint

    def wait_for_idle(self, reason: str) -> bool:
        """Block until the session has been quiet for IDLE_SECONDS.

        This is the contract the 'restarting at the next idle minute' log
        line advertises: an ACTIVE session is never interrupted. There is
        deliberately NO cap that forces the restart -- restart_child()
        terminate()s the child, and the child's in-flight drain
        (mcp_server._INFLIGHT / _DRAIN_IDLE_S) does NOT apply to a launcher
        SIGTERM, so a forced restart could kill a running generation. The
        cost of that choice -- a client that never goes idle defers the
        change indefinitely -- is disclosed by a WARNING every
        IDLE_WARN_EVERY_S rather than hidden. Returns False when the
        launcher is shutting down."""
        waited = 0.0
        while time.time() - self.last_activity < IDLE_SECONDS:
            if self.closing:
                return False
            time.sleep(5)
            waited += 5.0
            if waited % IDLE_WARN_EVERY_S < 5.0:
                log.warning(
                    "%s is still pending after %.0fs — this session has not "
                    "been idle for %ds yet, so the restart is being deferred "
                    "rather than interrupting it. It applies as soon as the "
                    "session goes quiet.",
                    reason,
                    waited,
                    IDLE_SECONDS,
                )
        return not self.closing

    def _respawn_delay(self, deliberate: bool) -> float:
        """Seconds to wait before bringing the child back.

        Every client sharing this install detects the same peer update on the
        same tick, so an undelayed respawn is a thundering herd against one
        venv -- JITTER is the fix for that. A repeated crash adds a small
        backoff, capped hard: the total delay must stay well inside
        pump_client_in's write-retry budget, or a client line that arrives
        mid-restart is dropped and the editor hangs on its id."""
        jitter = random.uniform(0.0, RESPAWN_JITTER_S)
        if deliberate:
            return jitter
        steps = max(0, self.respawn_failures - 1)
        backoff = RESPAWN_BACKOFF_BASE_S * (2 ** min(steps, 16))
        return min(RESPAWN_BACKOFF_MAX_S, backoff) + jitter

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
                if not self.wait_for_idle(reason):
                    continue
                # Re-check AFTER the wait. run_update_check() itself rewrites
                # .env (migrate_env appends newly shipped keys) and qa-doctor's
                # heal_env can restore it, so a change that is no longer
                # pending must not cost a restart.
                if status != "healed" and not self._env_changed_since_child_start():
                    log.info(
                        "%s is no longer pending after the idle wait — "
                        "not restarting.",
                        reason,
                    )
                    continue
                self.restart_child(reason)
            except Exception as exc:
                log.warning("Background update check failed (%s) — will retry.", exc)


def main() -> int:
    # stderr is rendered as '[error] ...' by MCP clients (Cursor showed
    # every updater INFO line as an error on the v1.38.0 validation run),
    # so stderr carries WARNING+ only and the INFO trail goes to this
    # PROCESS's own data/logs/launcher-<pid>.log. The threshold must sit
    # on the HANDLER: a root-level threshold alone re-leaks INFO the
    # moment the root drops to INFO for the file handler.
    _stderr = logging.StreamHandler(sys.stderr)
    _stderr.setLevel(logging.WARNING)
    _stderr.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    _root = logging.getLogger()
    _root.setLevel(logging.INFO)
    _root.addHandler(_stderr)
    try:
        from tools.log_setup import configure_file_logging

        _log_dir = INSTALL_DIR / "data" / "logs"
        # PER-PROCESS file (launcher-<pid>.log). Three MCP clients share
        # one install, so one shared RotatingFileHandler had three
        # supervisors rotating and truncating the same name and losing
        # each other's lines (2026-08-09). Same 1 MiB x 2 budget as
        # before -- only the file name and the per-line pid tag change.
        if configure_file_logging(
            _log_dir,
            prefix="launcher",
            max_bytes=1024 * 1024,
            backup_count=2,
        ) is None:
            raise OSError("no per-process launcher log file")
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

                _entry = "start.cmd" if sys.platform == "win32" else "start.sh"
                _start = str(INSTALL_DIR / _entry)
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
