#!/usr/bin/env python3
"""QA Agent Pro — self-updating MCP launcher (distribution edition).

Point your MCP client (Cursor / Claude Code / Claude Desktop) at start.sh.
This launcher supervises the real MCP server as a child process and keeps
the install current WITHOUT restarting the editor:

1. On start: a local MANIFEST.sha256 integrity check. A clean tree starts
   the server at once; a tampered one is healed (update check) first.
   The first update check, self-heal and read-only lock then run in the
   background as soon as the client has initialized.
2. While running: re-checks every QA_UPDATE_INTERVAL_MINUTES (default 15).
   A newer release installs in the background; the server then restarts
   itself once no tool is running (within one check interval) and the
   recorded MCP initialize handshake is replayed — the editor keeps its
   session, nothing to do. Set QA_DRIFT_RESTART_ENABLED=false to disable.
3. On SIGUSR1: the same reload, immediately, instead of waiting for the
   next poll -- this is what `qa-restart` sends. It still waits for the
   session to go idle, so a running generation is never interrupted.
4. If the server ever crashes it is respawned the same way.
5. qa-doctor is retried across such a reload automatically: the
   client makes ONE call and gets the final, post-reload report.

stdout carries the MCP protocol; every log line goes to stderr. A network
failure never blocks startup — the current version keeps serving."""

import hashlib
import json
import logging
import os
import random
import signal
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
# How long the watchdog waits for the client's initialize before its first
# update check anyway, so a client that never initializes still gets one.
FIRST_CHECK_WAIT_S = 120.0

# Used by the log-file dir below and the client-registration pass.
# Was referenced without being defined until 2026-08-04: both call
# sites are inside never-raise try blocks, so the NameError silently
# degraded launcher logging to INFO-on-stderr on every start and made
# QA_AUTO_REGISTER_CLIENTS a no-op.
from pathlib import Path as _PathLib

INSTALL_DIR = _PathLib(os.path.dirname(os.path.abspath(__file__)))

try:
    from tools.wire_log import open_wire_log

    WIRE = open_wire_log("launcher", INSTALL_DIR, track_replies=True)
except Exception:  # wire logging is optional: never block startup

    class WIRE:  # type: ignore[no-redef]
        @staticmethod
        def seen(direction, line):
            return False

        @staticmethod
        def set_lost_handler(handler):
            return None


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
    mcp_handlers.code_changed_since_start already applies to VERSION.
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


VENV_PROBE_IMPORT = "import dotenv, fastmcp, httpx, yaml"
VENV_PIP_TIMEOUT_S = 600.0
VENV_DIR = INSTALL_DIR / ".venv"
VENV_PYTHON = VENV_DIR / (
    "Scripts/python.exe" if os.name == "nt" else "bin/python"
)
VENV_LOCK = INSTALL_DIR / "data" / "venv.lock"


def running_in_venv() -> bool:
    """True when this process already runs from the install's own .venv."""
    try:
        return _PathLib(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        return False


def deps_present(python: str) -> bool:
    """Can `python` import what mcp_server.py imports? (A subprocess: the
    answer for a DIFFERENT interpreter cannot be read from this one.) A
    probe that times out (cold disk) counts as present: a slow answer is no
    reason to delete a working .venv."""
    try:
        proc = subprocess.run(
            [python, "-c", VENV_PROBE_IMPORT],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        log.warning("dependency probe of %s timed out; assuming present", python)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _pip_step() -> list:
    argv = [str(VENV_PYTHON), "-m", "pip", "install", "--quiet", "-e", "."]
    pins = INSTALL_DIR / "constraints.txt"
    return argv + ['-c', str(pins)] if pins.is_file() else argv


def run_step(argv: list) -> bool:
    """One bootstrap command in the install dir, stdin kept off the MCP
    pipe. Logs and returns False on failure."""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(INSTALL_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=VENV_PIP_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("venv bootstrap failed: %s (%s)", " ".join(argv), exc)
        return False
    if proc.returncode == 0:
        return True
    tail = proc.stdout.decode("utf-8", "replace").strip().splitlines()[-5:]
    log.warning("venv bootstrap failed (exit %s): %s\n%s",
                proc.returncode, " ".join(argv), "\n".join(tail))
    return False


def _under_lock(work) -> bool:
    """Run work() holding data/venv.lock, so a second launcher waits instead
    of racing pip or deleting a .venv mid-install. False when the lock
    cannot be made (a read-only install)."""
    import fcntl

    try:
        VENV_LOCK.parent.mkdir(parents=True, exist_ok=True)
        lock = open(VENV_LOCK, "a")
    except OSError as exc:
        log.warning("cannot lock %s: %s", VENV_LOCK, exc)
        return False
    with lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return bool(work())


def _create_venv() -> bool:
    """Fresh .venv, then pip. A failed step removes the half-built .venv so
    no later launch (nor the desktop app) takes it for a working one."""
    import shutil

    shutil.rmtree(VENV_DIR, ignore_errors=True)
    for argv in ([sys.executable, "-m", "venv", str(VENV_DIR)], _pip_step()):
        if not run_step(argv):
            shutil.rmtree(VENV_DIR, ignore_errors=True)
            return False
    log.warning("Backend dependencies installed into %s.", VENV_DIR)
    return True


def venv_healthy() -> bool:
    return VENV_PYTHON.is_file() and deps_present(str(VENV_PYTHON))


def build_venv() -> bool:
    """Create .venv beside launcher.py and `pip install -e .` into it --
    what install.sh does, for the install that was copied (DMG seed,
    manual unzip) instead of installed. Re-probes after the lock wait: the
    launcher that held the lock may have built it already."""
    log.warning(
        "Backend dependencies are missing for %s — creating %s and "
        "installing them (one-time, needs network).",
        sys.executable,
        VENV_DIR,
    )
    return _under_lock(lambda: venv_healthy() or _create_venv())


def _repair_in_venv() -> None:
    """Running from a .venv whose pip never finished (killed, or an older
    launcher): python runs but the deps are missing. Re-run pip into it;
    when that fails too the crash loop stays visible in the log."""
    if deps_present(sys.executable):
        return
    log.warning("%s lacks the backend dependencies — reinstalling.", VENV_DIR)
    _under_lock(lambda: deps_present(sys.executable) or run_step(_pip_step()))


def ensure_venv() -> None:
    """Re-exec this launcher from the install's .venv when the current
    interpreter cannot run the server. Never raises: when nothing can be
    done the launcher carries on as before and the crash loop stays
    visible in its log. POSIX only: os.execv on Windows forks, and the
    MCP stdio pipes do not survive that; install.ps1 builds the venv."""
    if os.name == "nt":
        return
    if running_in_venv():
        return _repair_in_venv()
    if deps_present(sys.executable):
        return  # a dev checkout or system python that carries the deps
    if not venv_healthy():
        if not build_venv():
            return
    log.info("Restarting from %s.", VENV_PYTHON)
    argv = [str(VENV_PYTHON), os.path.abspath(__file__)] + sys.argv[1:]
    try:
        os.execv(str(VENV_PYTHON), argv)
    except OSError as exc:
        log.warning("could not restart from %s: %s", VENV_PYTHON, exc)


ORPHAN_POLL_S = 5.0


def _client_gone(parent_pid: int) -> bool:
    """True once our client (the parent process) is no longer our parent.

    Several clients share one install, each with its own supervisor, so this
    only ever judges THIS supervisor's own parent. Not probed on Windows.
    """
    return os.name != "nt" and os.getppid() != parent_pid


def _end_orphan() -> None:
    log.info("Client process is gone — this supervisor exits.")
    os._exit(0)


def orphan_watch(sup, parent_pid: int, on_gone=_end_orphan) -> None:
    """Exit promptly when the client is gone (stdin EOF is the fast path)."""
    stop = threading.Event()
    while not sup.closing:
        if _client_gone(parent_pid):
            sup.closing = True
            with sup.child_lock:
                if sup.child is not None and sup.child.poll() is None:
                    sup.child.terminate()
            on_gone()
            return
        stop.wait(ORPHAN_POLL_S)


class ReplyLedger:
    """Ids the client already has an answer for. A respawn re-sends or
    replays requests, so the OLD and the NEW child can both answer one
    id; the client must see only the first. Bounded: oldest ids go
    first, and a request that reuses an id makes it answerable again."""

    LIMIT = 512

    def __init__(self) -> None:
        self._ids = {}
        self._lock = threading.Lock()

    def requested(self, request_id) -> None:
        """A client request with this id is now in flight."""
        with self._lock:
            self._ids.pop(repr(request_id), None)

    def is_repeat(self, msg) -> bool:
        """True when *msg* is a second reply to an id already answered."""
        if not isinstance(msg, dict) or msg.get("method") or "id" not in msg:
            return False
        if "result" not in msg and "error" not in msg:
            return False
        key = repr(msg["id"])
        with self._lock:
            if key in self._ids:
                return True
            self._ids[key] = True
            while len(self._ids) > self.LIMIT:
                del self._ids[next(iter(self._ids))]
        return False


# mcp_srv_idle.IDLE_EXIT_CODE -- the child's DELIBERATE 'nothing to do for
# a long while, free me' exit. The launcher keeps the client's stdio and
# respawns the child on the next client message. The literals MUST stay
# equal; tests/test_launcher_idle_out.py asserts it.
IDLE_EXIT_CODE = 87


class PendingRequests:
    """Client requests written to the child and not yet answered. A child
    that idles out can die with a request still unread in its pipe; those
    (and only those) are re-sent to the next child. Bounded: oldest first
    out."""

    LIMIT = 512

    def __init__(self) -> None:
        self._lines = {}
        self._lock = threading.Lock()

    def written(self, msg, line) -> None:
        """*line* reached the child; remember it when it wants a reply."""
        if not msg.get("method") or "id" not in msg:
            return
        with self._lock:
            self._lines[repr(msg["id"])] = line
            while len(self._lines) > self.LIMIT:
                del self._lines[next(iter(self._lines))]

    def answered(self, msg) -> None:
        """The child replied to this id (a reply has no method)."""
        if "id" in msg and not msg.get("method"):
            with self._lock:
                self._lines.pop(repr(msg["id"]), None)

    def unanswered(self) -> list:
        with self._lock:
            return list(self._lines.values())

    def discard(self, msg) -> None:
        """The write failed: forget the request again."""
        with self._lock:
            self._lines.pop(repr(msg.get("id")), None)


def wake_child(sup) -> None:
    """Respawn an idled-out child (no-op otherwise), replaying the handshake.
    On a spawn error the child stays idled, so the next call retries."""
    if not sup.idled:
        return
    with sup.child_lock:
        if not sup.idled or sup.closing:
            return
        log.info("Client message after idle-out: respawning the MCP server.")
        try:
            sup.spawn(replay=True)
        except Exception:
            log.warning("Respawn after idle-out failed; retrying on the next message.")
            _reap(sup.child)
            raise
        sup.idled = False


def _reap(child) -> None:
    """Kill a half-started child and free its pipe fds (a retry loop would leak them)."""
    if child is None:
        return
    try:
        if child.poll() is None:
            child.kill()
        child.wait()
    except Exception:
        pass
    for pipe in (child.stdin, child.stdout):
        try:
            pipe.close()
        except Exception:
            pass


def fail_request(sup, line) -> None:
    """Answer a request we cannot deliver with a JSON-RPC error."""
    msg = _as_dict(line)
    if not msg.get("method") or "id" not in msg:
        return
    sup.pending.answered({"id": msg["id"]})
    text = "The qa-agents server could not be restarted."
    error = {
        "jsonrpc": "2.0",
        "id": msg["id"],
        "error": {"code": -32603, "message": text},
    }
    wire = json.dumps(error).encode() + b"\n"
    # Tell the wire watchdog this id is answered: else it adds a second,
    # contradictory error ~110 s later. True = it already answered it.
    if not WIRE.seen("out", wire):
        sup.write_lost(wire)


def _mark_idle(sup, child):
    """Flag the idle-out and snapshot the unread requests in ONE critical
    section: wake_child needs child_lock, so a request that arrives after
    this is written to the NEW child and can never also be in the snapshot.
    None when this exit is stale (replaced child, closing)."""
    with sup.child_lock:
        if child is not sup.child or sup.closing:
            return None
        sup.idled = True
        sup.respawn_failures = 0
        return sup.pending.unanswered()


def _resend(sup, lost) -> None:
    try:
        wake_child(sup)
    except Exception:
        for line in lost:
            fail_request(sup, line)
        return
    with sup.child_lock:
        for line in lost:
            try:
                sup.child.stdin.write(line)
                sup.child.stdin.flush()
            except Exception:
                log.warning("Could not re-send a request after idle-out.")
                fail_request(sup, line)


def idle_out(sup, child) -> None:
    """The child exited with IDLE_EXIT_CODE: stay down until the client
    speaks, then re-send what it never read."""
    lost = _mark_idle(sup, child)
    if lost is None:
        return
    log.info("MCP server idled out (no tool or run) — it respawns on the next client message.")
    if lost:
        _resend(sup, lost)


RESUME_FILE = INSTALL_DIR / 'backups' / 'handshake-resume.jsonl'


def _resume_path_ok(path: str) -> bool:
    """--resume is honoured only for the one file this launcher writes."""
    try:
        return os.path.realpath(path) == os.path.realpath(str(RESUME_FILE))
    except (OSError, ValueError):
        return False


def _launcher_compiles() -> bool:
    """True when the launcher.py now on disk at least parses."""
    try:
        with open(os.path.abspath(__file__), "rb") as fh:
            compile(fh.read(), os.path.abspath(__file__), 'exec')
        return True
    except (OSError, SyntaxError, ValueError):
        return False


def _drop_resume(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _new_code_rejected(sup) -> bool:
    """True (and the hash stamped) when the new launcher.py does not parse."""
    if _launcher_compiles():
        return False
    log.error('New launcher.py does not parse; keeping the running one.')
    sup.self_hash = _self_hash()
    return True


def _exec_or_recover(sup, args: list, resume: str) -> None:
    """Re-exec; if execv fails keep this launcher and bring the child back."""
    try:
        os.execv(sys.executable, args)
    except OSError as exc:
        log.error('Launcher re-exec failed (%s); keeping the current one.', exc)
        sup.self_hash = _self_hash()
        sup.restarting = False
        if resume:
            _drop_resume(resume)
        sup.spawn(replay=bool(sup.handshake))


def install_tampered() -> bool:
    """Cheap integrity check for the --resume path (no update, no network)."""
    try:
        from tools.updater import integrity_problems

        return bool(integrity_problems(INSTALL_DIR))
    except ImportError:
        return False  # an older updater cannot check; the start already did
    except Exception:
        return True


def _resume_usable(path: str) -> bool:
    """True when --resume names our handshake file and the tree is intact."""
    if not _resume_path_ok(path):
        log.warning('Ignoring --resume %r: not the handshake file.', path)
        return False
    if install_tampered():
        log.warning('Integrity check failed; dropping the resume file.')
        _drop_resume(path)
        return False
    return True


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
# (handlers.reloading_message). Two ASCII-only pieces on
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
        # Set when the client's initialize is recorded; the watchdog's first
        # check waits on it. first_check_on_init is set by main() on a
        # non-resume start only.
        self.initialized = threading.Event()
        self.first_check_on_init = False
        self.swallow_id = None  # drop the child's reply to a REPLAYED initialize
        self.replies = ReplyLedger()  # one reply per request id, across respawns
        self.pending = PendingRequests()  # unanswered requests (idle-out resend)
        self.idled = False  # child exited on purpose; respawn on client traffic
        self.refresh_tools = False  # an update landed while idled: tell the client
        self.last_activity = time.time()
        self.intake = threading.Lock()  # held while a client line is handled
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
        self.setup_rearmed = False
        self.setup_wake = threading.Event()  # resolve/rearm wakes the guard
        # Serialises EVERY writer to the client stdio: the output pump,
        # spawn()'s tools/list_changed notice and release_setup_check()'s
        # timeout thread. A JSON-RPC line above PIPE_BUF is NOT an atomic
        # pipe write, so unsynchronised writers could interleave and
        # corrupt every in-flight call sharing this proxy. Reentrant, and
        # always the innermost lock -- nothing else is acquired under it.
        self.stdout_lock = threading.RLock()
        WIRE.set_lost_handler(self.write_lost)

    # ------------------------------------------------- child lifecycle
    def spawn(self, replay: bool) -> None:
        env = dict(os.environ)
        env["QA_MCP_ENABLED"] = "true"  # the server refuses to start without it
        env["MCP_LAUNCHER_IDLE_OK"] = "1"  # this launcher respawns it on demand
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
        self.rearm_setup_check()
        if replay and self.handshake and (not self.idled or self.refresh_tools):
            self.refresh_tools = False
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

    def client_busy(self) -> bool:
        """True while an exec would drop client traffic: unanswered requests,
        or (idled) activity inside the idle window."""
        if self.pending.unanswered():
            return True
        return self.idled and time.time() - self.last_activity < IDLE_SECONDS

    def restart_child(self, reason: str) -> None:
        # A changed launcher.py hot-swaps the whole process instead.
        if self.idled:
            # Down on purpose; the next client message spawns on the new state,
            # and that respawn must tell the client to re-fetch tool schemas.
            self.refresh_tools = True
            # A changed launcher.py must not wait for the next restart: no child
            # is running, so the re-exec only hands the handshake over.
            self.maybe_self_exec(reason)
            return
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
        if _new_code_rejected(self):
            return
        log.info("Launcher code changed on disk — re-exec (%s).", reason)
        with self.intake, self.child_lock:
            if self.client_busy():  # retried on the next update tick
                return
            self.restarting = True
            if self.child is not None and self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.child.kill()
            resume = str(RESUME_FILE)
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
            _exec_or_recover(self, args, resume)

    def write_lost(self, line: bytes) -> bool:
        """Answer the host with a lost-request error line (wire watchdog thread)."""
        try:
            with self.stdout_lock:
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
            return True
        except (OSError, ValueError):
            return False

    # ------------------------------------------------------------ pumps
    def _pump_child_out(self, child) -> None:
        out = sys.stdout.buffer
        for line in iter(child.stdout.readline, b""):
            self.last_activity = time.time()
            if WIRE.seen("out", line):
                continue  # late reply to a request already answered as lost
            if self.swallow_id is not None:
                try:
                    if json.loads(line).get("id") == self.swallow_id:
                        self.swallow_id = None
                        continue  # reply to the replayed initialize
                except ValueError:
                    pass
            if self.maybe_hold_setup_check(line):
                log.info("Held the qa-doctor reload notice (not sent to the client).")
                continue  # answered after the reload, in this same call
            self.pending.answered(_as_dict(line))
            if self.replies.is_repeat(_as_dict(line)):
                log.info("Dropped a second reply to an id the client already has.")
                continue  # old and new child both answered one request id
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
        if code == IDLE_EXIT_CODE:
            idle_out(self, child)
            return
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

    def forward_client_line(self, line: bytes) -> None:
        self.last_activity = time.time()
        WIRE.seen("in", line)
        msg = _as_dict(line)
        method = msg.get("method")
        if method and "id" in msg:
            self.replies.requested(msg["id"])
        if method == "initialize":
            self.handshake = [line]
            self.initialized.set()
            _write_session_state()
        elif method == "notifications/initialized" and self.handshake:
            self.handshake = self.handshake[:1] + [line]
        elif method == "tools/list":
            # The client re-fetched schemas — record the fresh version.
            _write_session_state()
        if _tool_call_name(msg) == SETUP_CHECK_TOOL:
            self.note_setup_check(msg.get("id"), line)
        for _attempt in range(CHILD_WRITE_RETRIES):
            # Recorded BEFORE the write (idle_out snapshots it); a failed
            # wake or write takes it back out and the next attempt retries.
            self.pending.written(msg, line)
            try:
                wake_child(self)  # a spawn error must not kill the launcher
                with self.child_lock:
                    child = self.child
                child.stdin.write(line)
                child.stdin.flush()
                break
            except Exception:
                self.pending.discard(msg)
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
            fail_request(self, line)  # never leave the editor waiting

    def pump_client_in(self) -> None:
        stdin = sys.stdin.buffer
        for line in iter(stdin.readline, b""):
            with self.intake:
                self.forward_client_line(line)
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
        self.setup_rearmed = False
        self.setup_wake.set()  # the guard re-reads state at once

    def rearm_setup_check(self) -> None:
        """A successor child was just spawned: give it its own window, once.
        The old child's exit + respawn can eat most of the first window, so
        the clock restarts when the progress event (the spawn) happens."""
        with self.setup_lock:
            if self.setup_held is None or self.setup_rearmed:
                return
            self.setup_rearmed = True
            self.setup_deadline = time.time() + SETUP_CHECK_TIMEOUT_S
            self.setup_wake.set()

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
            # Event-driven: wakes on resolve/rearm, else at the deadline.
            self.setup_wake.wait(remaining)
            self.setup_wake.clear()
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
        first = self.first_check_on_init
        while not self.closing:
            if first:
                # The start no longer checks for updates, so the first check
                # runs as soon as the client has initialized (bounded, so it
                # still runs when initialize never comes), then the interval.
                first = False
                self.initialized.wait(FIRST_CHECK_WAIT_S)
            else:
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


def _manual_restart(sup) -> None:
    """Reload the server because something asked us to, not because the
    update poll noticed a change: `qa-restart` (and anything else that can
    send SIGUSR1) gets an immediate reload instead of waiting up to
    QA_UPDATE_INTERVAL_MINUTES for the next tick.

    The idle wait is kept deliberately: restart_child() terminate()s the
    child and the child's in-flight drain does NOT apply to a launcher
    SIGTERM, so an immediate kill could destroy a running generation. The
    editor keeps its session either way -- spawn(replay=True) re-plays the
    recorded handshake."""
    reason = "manual restart request"
    try:
        # The watchdog may already be swapping the child; a second pass
        # would only restart what is about to be fresh anyway.
        if getattr(sup, "restarting", False):
            log.info("%s ignored — a restart is already in progress.", reason)
            return
        # Logged only once the wait SUCCEEDS: a launcher that is closing
        # never restarts, and claiming it did would misread the log.
        if not sup.wait_for_idle(reason):
            return
        log.info("%s — restarting the MCP server now.", reason)
        sup.restart_child(reason)
    except Exception as exc:  # a failed manual restart must not end the session
        log.warning("Manual restart failed (%s) — the current server keeps serving.", exc)


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
    # DMG seed / manual unzip ship no .venv: build it and re-exec (no-op in venv).
    ensure_venv()
    resume_path = ""
    if "--resume" in sys.argv:
        try:
            resume_path = sys.argv[sys.argv.index("--resume") + 1]
        except IndexError:
            resume_path = ""
    if resume_path and not _resume_usable(resume_path):
        resume_path = ''
    if not resume_path:
        try:
            from tools.updater import run_update_check, verify_integrity

            try:
                from tools.updater import integrity_problems
            except ImportError:
                # An installed updater older than this launcher has no
                # integrity_problems, and the outer except would skip the
                # check altogether: fall back to the older, weaker one.
                integrity_problems = verify_integrity

            # A clean tree starts the server at once: no network, no chmod
            # before the child. The first update check runs in the
            # background once the client has initialized. A tampered tree
            # (or a failed verify) heals FIRST, so tampered code never runs.
            try:
                tampered = integrity_problems(INSTALL_DIR)
            except Exception as exc:
                log.warning("Integrity check failed (%s) — healing first.", exc)
                tampered = [str(exc)]
            if tampered:
                status = run_update_check(
                    force=True, repo_override=DIST_REPO, lock_override=True
                )
                log.info("Startup update check: %s", status)
        except Exception as exc:  # never block startup
            log.warning("Update check failed (%s) — starting current version.", exc)
        # Fix 7 (the startup client-registration pass) was REMOVED on
        # 2026-08-13: QA_AUTO_REGISTER_CLIENTS was deleted along with the
        # rest of the kill-switch flag family and hardcoded to its
        # default, OFF, so this launcher no longer writes MCP entries into
        # editor config files outside the install dir, unattended, at
        # startup. An editor installed AFTER qa-agent-pro is picked up by
        # running ~/qa-agent-pro/connect.sh, which qa-doctor points at.
    sup = Supervisor()
    sup.first_check_on_init = not resume_path
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
    def _on_restart_signal(_sig, _frame):
        # Signal handlers must return immediately; the restart (which waits
        # for an idle session) belongs on its own thread.
        threading.Thread(target=_manual_restart, args=(sup,), daemon=True).start()

    try:
        signal.signal(signal.SIGUSR1, _on_restart_signal)
    except (AttributeError, ValueError, OSError):
        # Windows has no SIGUSR1, and a non-main thread cannot register one.
        # An install without on-demand restart still self-updates on its poll.
        log.debug("SIGUSR1 restart trigger unavailable on this platform")
    threading.Thread(target=sup.watchdog, daemon=True).start()
    threading.Thread(
        target=orphan_watch, args=(sup, os.getppid()), daemon=True
    ).start()
    sup.pump_client_in()  # blocks until the editor disconnects
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
