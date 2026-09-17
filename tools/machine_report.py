"""Machine-readable rows about THIS install, for a non-chat client.

The desktop app (``desktop/``) is an MCP client with no Python import path into
this tree, so every fact it shows a tester has to arrive through a tool reply.
``qa-doctor`` already establishes those facts -- but as PROSE, for a model to
read. Parsing that prose would make the desktop app a second, silent consumer of
a format nobody promised it.

So this module serves the SAME facts as rows, and the rule that keeps the two
surfaces from drifting is that it calls the same PRODUCERS the doctor calls and
re-derives nothing. ``PRODUCERS`` below names them, one entry per component, and
``tests/test_machine_report_tool_registered.py`` pins that every one of them is
still a symbol the doctor's own module reaches. That pin is STATIC on purpose:
calling ``handle_setup_check`` to compare surfaces would run a repairing,
network-touching handler inside a unit test.

**This module never writes.** Not a `.env`, not an MCP config, not a device.
``qa-doctor`` repairs; this reports. That split is why a desktop app can poll it
on a timer without a tester wondering what a refresh just changed.

Imports of the producers are LAZY, inside each builder, for the reason
``tools/flag_registry.py`` states about itself: importing this module must cost
nothing and can never create a cycle back into the composition root.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: The closed status vocabulary. ONE producer, one meaning: a consumer that
#: receives something outside this tuple has met a bug, not a new state, and a
#: client renders an unknown status as ``undetermined`` rather than silently
#: dropping the row -- a dropped row is how a machine looks healthy while a
#: component nobody rendered is failing.
STATUSES = ("ok", "warn", "fail", "off", "undetermined")

#: component -> ``(module path, attribute)`` of the producer whose answer the
#: row carries. The doctor reads the SAME symbols; the coupling pin walks this
#: table, so a row can never outlive the producer it claims to report.
PRODUCERS = {
    "backend_version": ("tools.updater", "_local_version"),
    "host_privileges": ("tools.host_privileges", "probe"),
    "mobile_sdk": ("tools.mobile.sdk_locator", "locate_sdk"),
    # Same producer as `mobile_sdk`, different QUESTION: the SDK root is what
    # the provisioner manages, the adb binary is what a mirror and the mobile
    # lane must BOTH drive. A desktop client that guesses `adb` off PATH can
    # end up on a second adb server, where a device is visible in one place
    # and absent in the other.
    "adb": ("tools.mobile.sdk_locator", "locate_sdk"),
}

#: How many rows ONE SECTION of a reply may carry (so `section="all"` carries at
#: most that many per section it contains). Bounds the payload a polling GUI parses
#: and renders, not the work: every builder below enumerates a bounded set
#: already (a fixed component list, the MCP clients on this machine, the
#: provisioner's own step list), so this is the backstop against a future
#: section that enumerates something device-sized -- a run's cases, a corpus --
#: and turns a polled reply into a multi-megabyte one. Applied in ONE place,
#: ``_rows``, so no builder can forget it and no two can disagree about it.
MAX_ROWS = 200

#: How old the machine-wide provisioning record may be before its rows are
#: reported as describing a PAST run. That record lives at a single path,
#: carries no run identity, and its only defence against being read as "what is
#: happening now" is its age -- a days-old error line has already been read as
#: live state. A polling client is the worst possible reader for that, so the
#: READER is made honest here rather than a reaper being added.
PROGRESS_STALE_S = 900

_SECTIONS = ("doctor", "clients", "provisioning", "backend")


@dataclass(frozen=True)
class Row:
    """One component's verdict, in the shape a table renders directly."""

    component: str
    status: str
    detail: str
    fix_hint: str = ""
    #: For a client row, the config file the WIZARD would edit. Empty for every
    #: other row. It is carried rather than re-derived because the app deciding
    #: which file to back up and merge must use the path the SERVER checked --
    #: two derivations of "where is Cursor's config" is how a backup lands
    #: beside one file and the merge lands in another.
    config_path: str = ""

    def as_dict(self) -> dict:
        out = asdict(self)
        if out["status"] not in STATUSES:
            out["status"] = "undetermined"
        return out


def _rows(items) -> list:
    """Serialise, normalise and CAP -- the one place the cap is applied."""
    out = []
    for item in items:
        if len(out) >= MAX_ROWS:
            logger.info("machine_report: row cap reached at %d", MAX_ROWS)
            break
        out.append(item.as_dict())
    return out


def install_dir() -> Path:
    """THIS install's root -- the directory ``tools/updater`` versions and the
    one a registered MCP entry must point at to be current.

    One producer for both questions: a second derivation of "where am I" is how
    a version row and a registration row end up disagreeing about the same
    install.
    """
    from tools.updater import _INSTALL_DIR

    return Path(_INSTALL_DIR)


def local_version() -> str | None:
    """The installed version, read from THIS install's directory.

    ``updater._local_version`` takes the install directory as an ARGUMENT -- it
    has no default -- so calling it bare raises, and a swallowed raise here
    would report every machine as "unknown" forever.
    """
    from tools.updater import _local_version

    return _local_version(install_dir())


def backend_info() -> dict:
    """Version AND edition in one call -- what a client needs to decide whether
    the backend it found is one it can talk to."""
    version, root = "unknown", ""
    try:
        version = str(local_version() or "unknown")
        root = str(install_dir())
    except Exception as exc:  # never raises: a client polls this
        logger.info("machine_report: version unavailable: %s", exc)
    edition = {}
    try:
        from tools import mcp_handlers

        edition = {
            "test_cases_only": bool(mcp_handlers._test_cases_only()),
            "mobile_modules_present": bool(mcp_handlers._mobile_modules_present()),
        }
    except Exception as exc:
        logger.info("machine_report: edition unavailable: %s", exc)
    return {"version": version, "install_dir": root, "edition": edition}


def doctor_rows() -> list:
    """The machine-readiness rows, from the doctor's own producers."""
    rows = []
    try:
        version = local_version()
        rows.append(
            Row(
                "backend_version",
                "ok" if version else "undetermined",
                str(version or "no version file in this install"),
                "" if version else "Reinstall, or re-run the launcher.",
            )
        )
    except Exception as exc:
        rows.append(
            Row(
                "backend_version",
                "undetermined",
                str(exc),
                "Reinstall, or re-run the launcher.",
            )
        )
    try:
        from tools import host_privileges

        # probe() returns the tools/ envelope {"error", "content"}; the flags
        # live INSIDE content. Reading them off the envelope made every machine
        # 'undetermined' with a dict repr for a detail (measured on the bundled
        # backend, 2026-09-16).
        envelope = host_privileges.probe() or {}
        if envelope.get("error"):
            raise RuntimeError(str(envelope["error"]))
        verdict = envelope.get("content") or {}
        unknown = bool(verdict.get("undetermined"))
        elevated = bool(verdict.get("can_elevate"))
        detail = (
            f"{verdict.get('os') or 'unknown os'} / {verdict.get('arch') or 'unknown arch'}: "
            + (
                "privilege state could not be determined"
                if unknown
                else ("can elevate" if elevated else "cannot elevate")
            )
            + (f" ({verdict['method']})" if verdict.get("method") else "")
        )
        rows.append(
            Row(
                "host_privileges",
                "undetermined" if unknown else ("ok" if elevated else "warn"),
                detail,
                ""
                if elevated and not unknown
                else "Admin-free steps only on this account.",
            )
        )
    except Exception as exc:
        rows.append(Row("host_privileges", "undetermined", str(exc)))
    try:
        from tools.mobile import sdk_locator

        located = (sdk_locator.locate_sdk() or {}).get("content") or {}
        root = str(located.get("sdk_root") or "")
        missing = list(located.get("missing") or [])
        rows.append(
            Row(
                "mobile_sdk",
                "ok" if root and not missing else ("warn" if root else "fail"),
                ("Android SDK at " + root if root else "No Android SDK found.")
                + (" Missing: " + ", ".join(missing) if missing else ""),
                ""
                if root and not missing
                else "The mobile provisioner installs these; the desktop "
                "wizard never writes into the SDK itself.",
            )
        )
        adb_path = str((located.get("tools") or {}).get("adb") or "")
        rows.append(
            Row(
                "adb",
                "ok" if adb_path else "fail",
                "adb at " + adb_path if adb_path else "No adb in the located SDK.",
                ""
                if adb_path
                else "Run the mobile provisioner's platform-tools step; this "
                "server installs it, a desktop client never should.",
            )
        )
    except Exception:
        rows.append(
            Row(
                "mobile_sdk",
                "off",
                "The mobile modules are not present in this edition.",
            )
        )
        rows.append(
            Row(
                "adb",
                "off",
                "The mobile modules are not present in this edition.",
            )
        )
    return _rows(rows)


def client_rows(home=None) -> list:
    """Which MCP clients are installed, and whether THIS install is registered.

    Read-only by construction: it calls ``discover_registrations``,
    ``default_targets`` and ``install_target`` and never ``register_entry`` /
    ``register_client`` / ``register_all``. Writing a client config is the
    WIZARD's job, with a backup beside the file; the server does not edit files
    outside its own tree on this path.

    "Current" is a claim about WHICH INSTALL the entry points at, not about the
    entry merely existing: a registration left behind by an older install is
    exactly the failure that made a prep stage on one install and finalize on
    another. It is decided by ``client_registry.install_target`` -- the same
    producer the doctor's split-install warning uses -- compared against
    ``install_dir()``.
    """
    rows = []
    try:
        from pathlib import Path as _Path

        from tools import client_registry

        found = []
        try:
            found = list(client_registry.discover_registrations(home) or [])
        except Exception as exc:
            logger.info("machine_report: discovery failed: %s", exc)
        mine = str(install_dir())
        by_config = {}
        for entry in found:
            if not isinstance(entry, dict):
                continue
            by_config.setdefault(str(entry.get("config") or ""), []).append(entry)
        for label, config_path, _dir in client_registry.default_targets(home):
            config_path = _Path(config_path)
            if not config_path.parent.exists():
                rows.append(
                    Row(
                        label,
                        "off",
                        "Not installed on this machine.",
                        "",
                        str(config_path),
                    )
                )
                continue
            entries = by_config.get(str(config_path)) or []
            if not entries:
                rows.append(
                    Row(
                        label,
                        "fail",
                        "Installed, with no qa-agents entry in its MCP config.",
                        "Let the setup wizard add the qa-agents entry.",
                        str(config_path),
                    )
                )
                continue
            targets = [
                client_registry.install_target(
                    str(e.get("command") or ""), e.get("base") or config_path.parent
                )
                for e in entries
            ]
            if mine in targets:
                rows.append(
                    Row(
                        label,
                        "ok",
                        "Registered, pointing at this install.",
                        "",
                        str(config_path),
                    )
                )
            else:
                rows.append(
                    Row(
                        label,
                        "warn",
                        "Registered, but pointing at another install: "
                        + ", ".join(t for t in targets if t),
                        "Re-register this install, or remove the stale entry -- "
                        "two installs live at once is how work stages on one "
                        "and finalizes on the other.",
                        str(config_path),
                    )
                )
        # Claude Code is NOT in `default_targets` -- it is registered through
        # `claude mcp add` rather than by editing a file this server knows the
        # path of, which is exactly why the loop above cannot see it. Decision
        # (6) names it as one of the three clients the wizard registers, so it
        # is detected HERE, read-only: the CLI on PATH is what proves it is
        # installed, and `~/.claude.json` is the config `discover_registrations`
        # already scans. Detection only -- the WRITE is the desktop app's, with
        # a backup beside the file.
        code_config = (
            _Path(home) / ".claude.json" if home else _Path.home() / ".claude.json"
        )
        code_present = bool(shutil.which("claude")) or code_config.is_file()
        if not code_present:
            rows.append(
                Row("Claude Code", "off", "Not installed on this machine.", "", "")
            )
        else:
            entries = by_config.get(str(code_config)) or []
            if not entries:
                rows.append(
                    Row(
                        "Claude Code",
                        "fail",
                        "Installed, with no qa-agents entry in its config.",
                        "Let the setup wizard add the qa-agents entry.",
                        str(code_config),
                    )
                )
            else:
                targets = [
                    client_registry.install_target(
                        str(e.get("command") or ""), e.get("base") or code_config.parent
                    )
                    for e in entries
                ]
                if mine in targets:
                    rows.append(
                        Row(
                            "Claude Code",
                            "ok",
                            "Registered, pointing at this install.",
                            "",
                            str(code_config),
                        )
                    )
                else:
                    rows.append(
                        Row(
                            "Claude Code",
                            "warn",
                            "Registered, but pointing at another install: "
                            + ", ".join(t for t in targets if t),
                            "Re-register this install, or remove the stale entry.",
                            str(code_config),
                        )
                    )
    except Exception as exc:
        rows.append(Row("mcp_clients", "undetermined", str(exc)))
    return _rows(rows)


def provisioning_rows(now: float | None = None) -> list:
    """Where mobile provisioning stands -- and HOW OLD that claim is.

    The kill-switch and ``apply=true`` rules are untouched: this reads a file
    and starts nothing.
    """
    stamp = time.time() if now is None else float(now)
    rows = []
    try:
        from tools.mobile import provisioner
    except Exception:
        return _rows(
            [
                Row(
                    "provisioning",
                    "off",
                    "The mobile modules are not present in this edition.",
                )
            ]
        )
    try:
        record = provisioner.read_progress()
    except Exception as exc:
        return _rows([Row("provisioning", "undetermined", str(exc))])
    content = record.get("content") if isinstance(record, dict) else None
    if not isinstance(content, dict) or not content:
        return _rows(
            [
                Row(
                    "provisioning",
                    "off",
                    "No provisioning has been started on this machine.",
                )
            ]
        )
    written_at = content.get("written_at")
    age = None
    if isinstance(written_at, (int, float)):
        age = max(0.0, stamp - float(written_at))
    stale = age is None or age > PROGRESS_STALE_S
    if content.get("error"):
        rows.append(
            Row(
                "provisioning",
                "warn" if stale else "fail",
                str(content.get("error")),
                "This record is older than the freshness window; re-check "
                "before believing it."
                if stale
                else "",
            )
        )
    for step in content.get("steps") or []:
        if not isinstance(step, dict):
            continue
        rows.append(
            Row(
                "provision_step:" + str(step.get("name") or "step"),
                "warn" if stale else str(step.get("status") or "undetermined"),
                str(step.get("detail") or ""),
            )
        )
    if not rows:
        rows.append(
            Row(
                "provisioning",
                "warn" if stale else "ok",
                "A provisioning record exists.",
            )
        )
    rows.append(
        Row(
            "provisioning_record_age",
            "warn" if stale else "ok",
            "unknown age" if age is None else ("%.0f seconds old" % age),
            "Older than the freshness window: this describes a PAST run, not "
            "necessarily what is happening now."
            if stale
            else "",
        )
    )
    return _rows(rows)


def collect(section: str = "all") -> dict:
    """The whole report, or one section of it. Never raises."""
    want = (section or "all").strip().lower()
    if want not in _SECTIONS and want != "all":
        return {"error": "unknown section: " + want, "sections": list(_SECTIONS)}
    out: dict = {"error": None, "section": want}
    if want in ("all", "backend"):
        out["backend"] = backend_info()
    if want in ("all", "doctor"):
        out["doctor"] = doctor_rows()
    if want in ("all", "clients"):
        out["clients"] = client_rows()
    if want in ("all", "provisioning"):
        out["provisioning"] = provisioning_rows()
    return out
