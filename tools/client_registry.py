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

POSIX only (``fcntl``). Windows registration stays manual -- a documented gap, not
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


def register_client(
    config_path: Path,
    start_command: str,
    *,
    server_name: str = SERVER_NAME,
    insert_only: bool = False,
    require_dir: Path | None = None,
) -> tuple[str, str]:
    """Ensure ``server_name`` is present in an MCP config. Returns (status, detail).

    ``insert_only=True`` (the startup pass): add the entry when absent and leave an
    existing one EXACTLY as the tester left it. ``insert_only=False``
    (``connect.sh``): also repair an entry whose command no longer matches, which
    is what makes re-running it useful after the install moves.

    ``require_dir`` is the client's own directory. When given and missing, this is
    a skip, not an error -- writing a config for an editor the tester does not have
    installed would be creating state for a product that is not there.

    Never raises.
    """
    config_path = Path(config_path)
    desired = {"command": str(start_command)}
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
        logger.debug("register_client failed for %s", config_path, exc_info=True)
        return ERROR, str(exc)
    finally:
        _unlock(fd)


def default_targets(home: Path | None = None) -> list[tuple[str, Path, Path]]:
    """(label, config_path, require_dir) for the JSON-config clients we know.

    Claude Code is NOT here: it is registered through ``claude mcp add`` rather
    than by editing a file, and shelling out to a CLI does not belong in a startup
    pass. Windows paths are absent -- see the module docstring.
    """
    home = Path(home) if home is not None else Path.home()
    out = [("Cursor", home / ".cursor" / "mcp.json", home / ".cursor")]
    if sys.platform == "darwin":
        app = home / "Library" / "Application Support" / "Claude"
    else:
        app = home / ".config" / "Claude"
    out.append(("Claude Desktop", app / "claude_desktop_config.json", app))
    return out


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
