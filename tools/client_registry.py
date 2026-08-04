"""Register this MCP server in an editor's own MCP config, safely.

ONE implementation, used by BOTH ``connect.sh`` (the interactive installer step)
and the launcher's startup pass, so the two cannot drift. Before this, the merge
logic lived only inside the ``CONNECT_SH`` template in ``scripts/build_dist.py``
as an inline Python heredoc, which meant a second caller had to copy it.

WHY A STARTUP PASS EXISTS AT ALL: registration used to happen exactly once, during
install. ``connect.sh`` skips a client whose config directory does not exist yet,
``install.sh`` refuses to re-run without ``QA_FORCE``, and the launcher never
called ``connect.sh`` -- so an editor installed AFTER qa-agent-pro was never picked
up, and the tester had no way to know that ``connect.sh`` needed re-running.

HOUSE RULES THIS MODULE OBEYS:

* **stdlib only.** The launcher deliberately imports nothing internal except
  ``tools.updater`` so that a self-update can swap code before it is loaded. This
  module therefore imports no project module either, and takes the command and
  the target paths as arguments rather than reading settings itself.
* **insert-only is a CHOICE, not the default behaviour of one function.**
  ``connect.sh`` deliberately REPAIRS a stale entry (the install path may have
  moved), while the startup pass must never rewrite an entry a tester hand-tuned.
  Same code, explicit ``insert_only`` flag, so neither caller can inherit the
  other's semantics by accident.
* **Atomic and locked.** Three editors share one install here, so a startup pass
  can mean concurrent read-modify-write on the same file. Writes go to a temp file
  in the same directory and are ``os.replace``d, under an advisory lock.
* **``.bak`` only when a write actually happens.** The old code copied the backup
  before every write attempt, including no-ops, so a re-run could overwrite a
  good backup with identical content and a crash mid-write left no earlier copy.
* **Malformed config is never clobbered.** Unparseable JSON returns an error and
  writes nothing: losing a tester's other MCP servers is far worse than not
  registering.

Advisory locking is POSIX only (``fcntl``) and simply degrades on Windows; the
CONFIG PATHS are cross-platform, so native-Windows installs register the same
way. What remains Windows-specific is only the lock -- a narrow gap, not
an oversight.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("qa_agents.client_registry")

SERVER_NAME = "qa-agent-pro"

# The HOSTED Atlassian MCP server (Jira Cloud, OAuth 2.1). Jira is read through
# the tester's OWN connection, so this entry has to exist in THEIR client config
# -- and writing it is the difference between "paste a ticket URL" and "hand-edit
# a JSON file", which is where non-technical testers stop.
#
# It only CONFIGURES the server. The OAuth click still happens inside the editor,
# and no caller may report Jira as connected on the strength of this write.
#
# The same URL appears in tools/jira_mcp.py's instruction text. It is duplicated
# on purpose: this module imports no project module by house rule (see the module
# docstring), so the two are kept in step by hand.
ATLASSIAN_NAME = "atlassian"
ATLASSIAN_ENTRY = {"type": "http", "url": "https://mcp.atlassian.com/v1/mcp/authv2"}

# Status strings, so callers can report without re-deriving intent.
ADDED = "added"
UPDATED = "updated"
PRESENT = "present"
SKIPPED = "skipped"
ERROR = "error"


def _lock(path: Path):
    """Advisory exclusive lock on a sidecar file. Returns an fd, or None.

    A sidecar (not the config itself) so the lock survives the ``os.replace`` that
    swaps the config inode. Best effort: a platform without ``fcntl`` proceeds
    unlocked rather than refusing to register.
    """
    try:
        import fcntl

        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except Exception:
        logger.debug("could not acquire an MCP-config lock; proceeding", exc_info=True)
        return None


def _unlock(fd) -> None:
    if fd is None:
        return
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        logger.debug("could not release the MCP-config lock", exc_info=True)
    finally:
        try:
            os.close(fd)
        except Exception:
            logger.debug("could not close the MCP-config lock fd", exc_info=True)


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the SAME directory, then ``os.replace``.

    Same-directory matters: ``os.replace`` is only atomic within one filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def register_entry(
    config_path: Path,
    server_name: str,
    entry: dict,
    *,
    insert_only: bool = False,
    require_dir: Path | None = None,
) -> tuple[str, str]:
    """Ensure ``server_name`` maps to ``entry`` in an MCP config. (status, detail).

    ``entry`` is the server object installed verbatim: a stdio
    ``{"command": ...}`` for this server, or a remote
    ``{"type": "http", "url": ...}`` for a hosted one such as Atlassian.
    Generalized 2026-08-04 -- the body hardcoded the stdio shape, so NO caller
    could add the Atlassian entry and every tester was told to hand-edit
    ``mcpServers`` JSON, which is the step non-technical testers fail at.

    ``insert_only=True`` (the startup pass, and every THIRD-PARTY entry): add the
    entry when absent and leave an existing one EXACTLY as the tester left it.
    ``insert_only=False`` (``connect.sh``, for OUR OWN entry): also repair an entry
    whose value no longer matches, which is what makes re-running it useful after
    the install moves.

    ``require_dir`` is the client's own directory. When given and missing, this is
    a skip, not an error -- writing a config for an editor the tester does not have
    installed would be creating state for a product that is not there.

    Never raises.
    """
    config_path = Path(config_path)
    if not isinstance(entry, dict):
        # "Never raises" is a CONTRACT here -- the launcher's startup pass calls
        # through this function, and the shape it replaced
        # (``{"command": str(start_command)}``) could not fail. So a bad entry is
        # an error RETURN, not a TypeError escaping into startup.
        return ERROR, "entry must be a JSON object"
    desired = dict(entry)
    if require_dir is not None and not Path(require_dir).is_dir():
        return SKIPPED, "client not detected"

    fd = _lock(config_path)
    try:
        cfg: dict = {}
        original: str | None = None
        if config_path.is_file():
            try:
                original = config_path.read_text(encoding="utf-8")
                stripped = original.strip()
                cfg = json.loads(stripped) if stripped else {}
            except (OSError, ValueError) as exc:
                # Never clobber something we cannot parse.
                return ERROR, f"existing config is not readable JSON ({exc})"
            if not isinstance(cfg, dict):
                return ERROR, "existing config root is not a JSON object"

        servers = cfg.get("mcpServers")
        if servers is None:
            servers = {}
            cfg["mcpServers"] = servers
        if not isinstance(servers, dict):
            return ERROR, "existing mcpServers is not a JSON object"

        existing = servers.get(server_name)
        if existing is not None:
            if insert_only:
                return PRESENT, "already registered; left untouched"
            if existing == desired:
                return PRESENT, "already registered with this command"
            status = UPDATED
        else:
            status = ADDED

        servers[server_name] = desired
        payload = json.dumps(cfg, indent=2) + "\n"
        if original is not None and payload == original:
            return PRESENT, "already up to date"

        # .bak ONLY now that a real write is about to happen.
        if original is not None:
            try:
                _atomic_write(Path(str(config_path) + ".bak"), original)
            except Exception:
                logger.debug("could not write a .bak", exc_info=True)
        _atomic_write(config_path, payload)
        return status, str(config_path)
    except Exception as exc:
        logger.debug("register_entry failed for %s", config_path, exc_info=True)
        return ERROR, str(exc)
    finally:
        _unlock(fd)


def register_client(
    config_path: Path,
    start_command: str,
    *,
    server_name: str = SERVER_NAME,
    insert_only: bool = False,
    require_dir: Path | None = None,
) -> tuple[str, str]:
    """Ensure THIS server (a stdio ``command`` entry) is registered.

    A thin delegate to ``register_entry`` so the merge/lock/backup logic has
    exactly one implementation. Signature and semantics are unchanged from when
    this function held that logic itself. Never raises.
    """
    return register_entry(
        config_path,
        server_name,
        {"command": str(start_command)},
        insert_only=insert_only,
        require_dir=require_dir,
    )


def default_targets(home: Path | None = None) -> list[tuple[str, Path, Path]]:
    """(label, config_path, require_dir) for the JSON-config clients we know.

    Claude Code is NOT here: it is registered through ``claude mcp add`` rather
    than by editing a file, and shelling out to a CLI does not belong in a startup
    pass. Windows IS covered (``%APPDATA%\\Claude``) as of native-Windows
    support; only the advisory lock degrades there -- see the module docstring.
    """
    home = Path(home) if home is not None else Path.home()
    out = [("Cursor", home / ".cursor" / "mcp.json", home / ".cursor")]
    if sys.platform == "darwin":
        app = home / "Library" / "Application Support" / "Claude"
    elif sys.platform == "win32":
        # %APPDATA% by its conventional path rather than the env
        # var, so a caller-supplied ``home`` still governs (tests, and
        # an install
        # driven for another profile). Cursor needs no branch at all:
        # ``~/.cursor`` is already correct on Windows.
        app = home / "AppData" / "Roaming" / "Claude"
    else:
        app = home / ".config" / "Claude"
    out.append(("Claude Desktop", app / "claude_desktop_config.json", app))
    return out


# A project-scope MCP config registers a server only while that folder is open,
# which is how a SECOND qa server appears without anyone editing a user config.
_PROJECT_CONFIG_RELPATHS = (".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json")


def _looks_like_qa_server(name: str) -> bool:
    """Whether an MCP server entry name is one of ours.

    Deliberately name-based and generous (`qa-agents`, `qa-agent-pro`,
    `qa_agent_dev`, ...): the point is to notice a SECOND one, so a false positive
    costs an informational line while a false negative hides the split entirely.
    """
    low = (name or "").lower()
    return "qa" in low and ("agent" in low or "agents" in low)


def discover_registrations(
    home: Path | None = None,
    workspace_roots: list | None = None,
) -> list[dict]:
    """Every qa-server MCP registration visible on this machine. READ-ONLY.

    2026-08-03. A real run staged its prep on one install and finalized it on
    another, because the packaged install is registered in the USER configs while
    this project's own `.mcp.json` / `.cursor/mcp.json` register a DEV checkout --
    so anyone who opens the repo has two qa servers live at once and an agent can
    pick a different one per call. The version-skew warning catches it after the
    fact; this is how `qa-doctor` can warn BEFORE a suite is built.

    Returns dicts of {scope, name, command, config}. Never raises: a missing or
    malformed config is skipped, because this only produces an advisory line.
    """
    out: list[dict] = []
    home = Path(home) if home is not None else Path.home()
    seen_paths: set = set()

    def _scan(path: Path, scope: str, base: object = None) -> None:
        try:
            rp = path.resolve()
            if rp in seen_paths or not path.is_file():
                return
            seen_paths.add(rp)
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
            servers = (data or {}).get("mcpServers") or {}
            if not isinstance(servers, dict):
                return
            for name, entry in servers.items():
                if not _looks_like_qa_server(str(name)):
                    continue
                cmd = ""
                if isinstance(entry, dict):
                    cmd = str(entry.get("command") or entry.get("url") or "")
                    args = entry.get("args")
                    if isinstance(args, list) and args:
                        cmd = (cmd + " " + " ".join(str(a) for a in args)).strip()
                out.append(
                    {
                        "scope": scope,
                        "name": str(name),
                        "command": cmd,
                        "config": str(path),
                        # A relative path in a PROJECT config is workspace-relative,
                        # not config-relative -- `${workspaceFolder}` means the
                        # workspace ROOT. Resolving against the config's own folder
                        # made `.cursor/mcp.json` look like a third install living in
                        # `<root>/.cursor`. Carry the correct base with the row.
                        "base": str(base or path.parent),
                    }
                )
        except Exception:
            logger.debug("could not scan %s for MCP registrations", path, exc_info=True)

    _scan(home / ".claude.json", "user")
    for _label, cfg, _need in default_targets(home):
        _scan(cfg, "user")
    for root in list(workspace_roots or []):
        for rel in _PROJECT_CONFIG_RELPATHS:
            _scan(Path(root) / rel, "project", base=Path(root))
    return out


def install_target(command: str, base: str | Path) -> str:
    """The install DIRECTORY a registration points at, for grouping.

    Grouping on the raw command over-counts, and a warning that inflates its own
    number is worse than none: the same dev checkout is spelled
    ``.venv/bin/python mcp_server.py`` in one config and
    ``${workspaceFolder}/.venv/bin/python ${workspaceFolder}/mcp_server.py`` in
    another, which naive grouping reports as two separate servers.

    So resolve to the directory: take the last `.py`/`.sh` token (the server
    script, not the interpreter), strip `${workspaceFolder}` placeholders, resolve
    a relative path against the CONFIG's own directory -- which is what a
    project-scope relative path actually means -- and return its parent. Falls back
    to the raw command when no script token is present, so unrelated servers are
    never silently merged. Never raises.
    """
    try:
        raw = str(command or "").strip()
        if not raw:
            return ""
        cleaned = raw.replace("${workspaceFolder}/", "").replace(
            "${workspaceFolder}", ""
        )
        tokens = [t for t in cleaned.split() if t]
        script = next(
            (t for t in reversed(tokens) if t.endswith((".py", ".sh"))),
            "",
        )
        if not script:
            return raw
        p = Path(script)
        if not p.is_absolute():
            p = Path(base) / p
        return str(p.resolve().parent)
    except Exception:
        logger.debug("install_target failed for %r", command, exc_info=True)
        return str(command or "")


def split_server_warning(
    home: Path | None = None,
    workspace_roots: list | None = None,
) -> str:
    """One advisory line when MORE THAN ONE distinct qa INSTALL is registered.

    Grouped by resolved install directory, not by name or raw command: three
    clients all pointing at the same packaged install is normal and silent. Two
    different installs is the hazard -- an agent can prepare against one and
    submit against the other, and they have separate `.env` files, so the suite is
    prepared under one set of feature flags and finalized under another. Returns
    "" when there is nothing to say. Never raises.
    """
    try:
        found = discover_registrations(home, workspace_roots)
        by_install: dict = {}
        for r in found:
            key = (
                install_target(r["command"], r.get("base") or r["config"]) or r["name"]
            )
            by_install.setdefault(key, []).append(r)
        if len(by_install) < 2:
            return ""
        lines = []
        for target, rows in sorted(by_install.items()):
            names = ", ".join(sorted({r["name"] for r in rows}))
            scopes = ",".join(sorted({r["scope"] for r in rows}))
            lines.append(f"`{names}` ({scopes}) at `{target}`")
        return (
            f"**{len(by_install)} DIFFERENT qa-agents installs are registered** "
            "in your MCP configs: "
            + "; ".join(lines)
            + ". Run one whole flow against ONE of them. An agent that prepares "
            "against one and submits to the other is mixing two installs, which "
            "have separate `.env` files and therefore different feature flags -- "
            "the suite then gets prepared under one configuration and finalized "
            "under another. A project-scope entry only exists while that folder is "
            "open, so this is easy to hit without having changed anything."
        )
    except Exception:
        logger.debug("split_server_warning failed", exc_info=True)
        return ""


def register_all(
    start_command: str,
    *,
    home: Path | None = None,
    insert_only: bool = False,
) -> list[tuple[str, str, str]]:
    """Run ``register_client`` over ``default_targets``. Returns per-client results.

    Never raises: one client's failure must not stop the others, and must never
    stop a server from starting.
    """
    results: list[tuple[str, str, str]] = []
    for label, path, need in default_targets(home):
        try:
            status, detail = register_client(
                path, start_command, insert_only=insert_only, require_dir=need
            )
        except Exception as exc:  # pragma: no cover - register_client never raises
            status, detail = ERROR, str(exc)
        results.append((label, status, detail))
    return results


def atlassian_targets(home: Path | None = None) -> list[tuple[str, Path, Path]]:
    """(label, config_path, require_dir) for clients whose Atlassian entry is a FILE.

    Cursor only -- and the omissions are deliberate, not partial work:

    * **Claude Desktop** reaches Atlassian through a HOSTED Connector
      (Settings -> Connectors). There is no local entry to merge, and writing an
      ``atlassian`` object into ``claude_desktop_config.json`` would connect
      nothing while looking like it had.
    * **Claude Code** and **Gemini CLI** register through their own CLIs
      (``claude mcp add --scope user --transport http`` / ``gemini mcp add``).
      Shelling out to a CLI does not belong in this module -- the connect scripts
      do that half, and the SCOPE argument is the reason it cannot be inferred
      here: only the caller knows whether this is a user-wide install.
    """
    home = Path(home) if home is not None else Path.home()
    return [("Cursor", home / ".cursor" / "mcp.json", home / ".cursor")]


def register_atlassian(
    *, home: Path | None = None, insert_only: bool = True
) -> list[tuple[str, str, str]]:
    """Insert the hosted Atlassian entry for each file-configured client.

    ``insert_only`` defaults to **True**, unlike ``register_all``: this is a THIRD
    PARTY's entry and an existing one may be authorized or hand-tuned. Repairing
    our own stale command is a different situation from rewriting someone else's.

    This writes the entry; it does NOT authorize it. OAuth happens in the editor,
    so callers must phrase the result as "configured, one click left" and never as
    "Jira is connected" -- the same gap tools/jira_mcp.connect_hint_line() is
    already careful about.

    Never raises: one client's failure must not stop the others.
    """
    results: list[tuple[str, str, str]] = []
    for label, path, need in atlassian_targets(home):
        try:
            status, detail = register_entry(
                path,
                ATLASSIAN_NAME,
                ATLASSIAN_ENTRY,
                insert_only=insert_only,
                require_dir=need,
            )
        except Exception as exc:  # pragma: no cover - register_entry never raises
            status, detail = ERROR, str(exc)
        results.append((label, status, detail))
    return results
