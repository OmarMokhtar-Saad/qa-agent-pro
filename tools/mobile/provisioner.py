"""Take a machine from "nothing installed" to "an AVD exists", idempotently.

The design is a PLAN, not a script: :func:`plan` inspects the machine and
returns one dict per step carrying ``satisfied``. :func:`run` executes only the
unsatisfied ones, so a second call on a provisioned machine issues zero
commands -- which is the property the test asserts over the recorded argv list,
not over a log line.

Two steps can be *blocked* rather than satisfied or pending: the JRE and
cmdline-tools downloads need a pinned SHA-256 that this repo does not yet have
(no release has been cut). They report ``state="blocked_unpinned"`` with a fix
line instead of guessing, for the same reason ``ime.py`` does: a placeholder
hash is worse than a refusal, because it turns "we cannot verify this" into "we
verified this against nothing". Neither step is reached on any machine that
already has Android Studio, which is every machine this phase was measured on.

Runs as a **detached OS process** (:func:`start_detached`) launched via
``sys.executable -m tools.mobile.provisioner``, never inside the MCP process:
a 2.2GB download inside the server would blow every client timeout it has.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from config.settings import settings
from tools.mobile import downloader, paths, platform_info, sdk_locator

logger = logging.getLogger(__name__)

#: The AVD this lane creates and re-attaches to. A fixed name is what makes
#: re-attach after an MCP server restart possible at all.
AVD_NAME = "qa-agents-api35"
AVD_DEVICE = "pixel_7"
ANDROID_API = "android-35"
SYSTEM_IMAGE_TAG = "google_apis_playstore"

#: Progress / result file inside the cache's ``state/`` dir.
PROGRESS_FILE = "provision.json"

#: sdkmanager packages, in install order.
SDK_PACKAGES: tuple[str, ...] = ("platform-tools", "emulator")

#: Worst-case download footprint, used for the pre-flight disk check. Measured
#: from Google's own component sizes; deliberately generous.
WORST_CASE_BYTES = int(2.6 * 1024 * 1024 * 1024)

#: Per-command timeout. sdkmanager unpacks a system image, which is slow.
STEP_TIMEOUT_S = 1800


def download_cap_bytes() -> int:
    """The operator's ceiling on a provisioning download, in bytes.

    ``QA_MOBILE_DOWNLOAD_MAX_GB``. This is the field's ONLY reader, and it is a
    real one: when the worst-case footprint exceeds the ceiling, :func:`run`
    refuses BY NAME with both numbers rather than starting a download the
    operator has said they do not want.
    """
    try:
        return int(float(settings.qa_mobile_download_max_gb) * (1024**3))
    except Exception:  # pragma: no cover - the coercer guarantees a float
        return int(4.0 * (1024**3))


#: Step states.
SATISFIED = "satisfied"
PENDING = "pending"
BLOCKED_UNPINNED = "blocked_unpinned"

_UNPINNED_FIX = (
    "No qa-agents release pins this component's SHA-256 yet, so it cannot be "
    "downloaded and verified. Install Android Studio (which supplies the SDK "
    "and a Java runtime) and re-run; the mobile lane reuses an existing SDK "
    "and never downloads one it can find."
)


def system_image() -> str:
    """The ``system-images;...`` package id for this host's ABI."""
    info = (platform_info.host_info() or {}).get("content") or {}
    abi = str(info.get("image_abi") or platform_info.X86_64_ABI)
    return ";".join(("system-images", ANDROID_API, SYSTEM_IMAGE_TAG, abi))


def _run_sync(cmd: list[str], timeout: int = STEP_TIMEOUT_S) -> tuple[int, str, str]:
    """The subprocess seam for provisioning commands. No shell, always a timeout."""
    return platform_info._run_sync(cmd, timeout=timeout)


def _spawn(cmd: list[str], **kwargs) -> int:
    """The detached-spawn seam. Returns the child pid."""
    proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 - argv list, no shell
    return int(proc.pid)


def _repo_root() -> Path:
    """The directory ``-m tools.mobile.provisioner`` must run from."""
    return Path(__file__).resolve().parents[2]


def _avd_exists(name: str) -> bool:
    try:
        return (Path.home() / ".android" / "avd" / (str(name) + ".ini")).is_file()
    except OSError:
        return False


def _sdk_package_installed(sdk_root: str, package: str) -> bool:
    """True when *package*'s directory already exists under *sdk_root*.

    Deliberately a filesystem test rather than ``sdkmanager --list_installed``:
    that call costs 10-20 seconds and a network round trip, and this function
    is called on every plan, including the one a preflight renders.
    """
    if not sdk_root:
        return False
    root = Path(sdk_root)
    parts = str(package).split(";")
    try:
        return root.joinpath(*parts).is_dir()
    except OSError:
        return False


def plan() -> dict:
    """Inspect the machine. ``{"error", "content": {"steps": [...], ...}}``.

    Each step is ``{id, title, state, detail, command, fix}``. ``command`` is
    an argv list (empty for steps that are pure bookkeeping) and is what
    :func:`run` would execute -- so the dry run prints exactly what the real
    run would do, rather than a prose approximation of it.
    """
    try:
        host = (platform_info.host_info() or {}).get("content") or {}
        sdk = (sdk_locator.locate_sdk() or {}).get("content") or {}
        java = (sdk_locator.locate_java() or {}).get("content") or {}
        sdk_root = str(sdk.get("sdk_root") or "")
        tools = dict(sdk.get("tools") or {})
        steps: list[dict] = []

        steps.append(
            {
                "id": "cache",
                "title": "mobile cache directories",
                "state": SATISFIED if paths.cache_root().is_dir() else PENDING,
                "detail": str(paths.cache_root()),
                "command": [],
                "fix": "",
            }
        )

        if not host.get("emulator_ok", False):
            steps.append(
                {
                    "id": "host",
                    "title": "supported host",
                    "state": BLOCKED_UNPINNED,
                    "detail": str(host.get("reason") or "unsupported host"),
                    "command": [],
                    "fix": "",
                }
            )
            return {
                "error": None,
                "content": {
                    "host": host,
                    "sdk": sdk,
                    "java": java,
                    "avd": AVD_NAME,
                    "system_image": "",
                    "steps": steps,
                },
            }

        steps.append(
            {
                "id": "java",
                "title": "Java runtime for sdkmanager/avdmanager",
                "state": SATISFIED if java.get("found") else BLOCKED_UNPINNED,
                "detail": (
                    str(java.get("java")) + " (" + str(java.get("source")) + ")"
                    if java.get("found")
                    else "no Java runtime found"
                ),
                "command": [],
                "fix": "" if java.get("found") else _UNPINNED_FIX,
            }
        )

        sdkmanager = str(tools.get("sdkmanager") or "")
        steps.append(
            {
                "id": "cmdline-tools",
                "title": "Android command-line tools",
                "state": SATISFIED if sdkmanager else BLOCKED_UNPINNED,
                "detail": sdkmanager or "sdkmanager not found in any known SDK",
                "command": [],
                "fix": "" if sdkmanager else _UNPINNED_FIX,
            }
        )

        # Licences: auto-accept ONLY where Android Studio is absent (the user's
        # decision). Where Studio exists the tester owns their licence state.
        studio = sdk_locator.studio_present()
        steps.append(
            {
                "id": "licenses",
                "title": "SDK licences",
                "state": SATISFIED if (studio or not sdkmanager) else PENDING,
                "detail": (
                    "Android Studio is installed, so licences are left to it"
                    if studio
                    else "auto-accepted because Android Studio is absent"
                ),
                "command": (
                    []
                    if (studio or not sdkmanager)
                    else [sdkmanager, "--licenses", "--sdk_root=" + sdk_root]
                ),
                "fix": "",
            }
        )

        image = system_image()
        for package in SDK_PACKAGES + (image,):
            installed = _sdk_package_installed(sdk_root, package)
            steps.append(
                {
                    "id": "sdk:" + package,
                    "title": "SDK package " + package,
                    "state": (
                        SATISFIED
                        if installed
                        else (PENDING if sdkmanager else BLOCKED_UNPINNED)
                    ),
                    "detail": (
                        "present under " + sdk_root
                        if installed
                        else "not installed under " + (sdk_root or "(no SDK)")
                    ),
                    "command": (
                        []
                        if installed or not sdkmanager
                        else [sdkmanager, "--sdk_root=" + sdk_root, package]
                    ),
                    "fix": "" if installed or sdkmanager else _UNPINNED_FIX,
                }
            )

        avdmanager = str(tools.get("avdmanager") or "")
        avd_there = _avd_exists(AVD_NAME)
        steps.append(
            {
                "id": "avd",
                "title": "AVD " + AVD_NAME,
                "state": (
                    SATISFIED
                    if avd_there
                    else (PENDING if avdmanager else BLOCKED_UNPINNED)
                ),
                "detail": (
                    "exists"
                    if avd_there
                    else "will be created from " + image + " as " + AVD_DEVICE
                ),
                "command": (
                    []
                    if avd_there or not avdmanager
                    else [
                        avdmanager,
                        "create",
                        "avd",
                        "-n",
                        AVD_NAME,
                        "-d",
                        AVD_DEVICE,
                        "-k",
                        image,
                        "--force",
                    ]
                ),
                "fix": "" if avd_there or avdmanager else _UNPINNED_FIX,
            }
        )
        return {
            "error": None,
            "content": {
                "host": host,
                "sdk": sdk,
                "java": java,
                "avd": AVD_NAME,
                "system_image": image,
                "steps": steps,
            },
        }
    except Exception as exc:
        logger.exception("mobile.provisioner.plan failed")
        return {"error": str(exc), "content": None}


def _progress_path() -> Path:
    return paths.state_file(PROGRESS_FILE)


def _publish(payload: dict) -> None:
    downloader.write_progress(_progress_path(), payload)


def read_progress() -> dict:
    """The detached run's latest state, or an empty content when there is none."""
    try:
        target = _progress_path()
        if not target.is_file():
            return {"error": None, "content": None}
        return {
            "error": None,
            "content": json.loads(target.read_text(encoding="utf-8")),
        }
    except Exception as exc:
        logger.info("mobile.provisioner: unreadable progress file: %s", exc)
        return {"error": str(exc), "content": None}


def run(apply: bool = False) -> dict:
    """Execute the pending steps (``apply=True``) or report them.

    ``{"error", "content": {"executed": [...], "would_run": [...],
    "blocked": [...], "steps": [...]}}``. With ``apply=False`` NOTHING is
    spawned -- not even a version probe -- which is what makes ``--dry-run``
    safe to offer a tester who has not yet agreed to a 2.2GB download.
    """
    try:
        # The kill-switch belongs HERE, not only at start_detached: this is the
        # function that downloads 2.2GB and creates an AVD, and `python -m
        # tools.mobile.provisioner --apply` reaches it without passing the
        # other check. A guard on the parent process does not guard the child.
        # Reporting (apply=False) stays available with the flag off -- it
        # spawns nothing, and a tester deciding whether to opt in needs to see
        # what would happen.
        if apply and not settings.qa_mobile_run_enabled:
            # Publish like the sibling refusals below, or a UI polling the
            # progress file reads a stale phase from an earlier run.
            _publish(
                {
                    "phase": "error",
                    "pct": 0,
                    "bytes": 0,
                    "message": "refused: QA_MOBILE_RUN_ENABLED is off",
                    "error": "kill-switch off",
                }
            )
            return {
                "error": (
                    "Refusing to provision: the mobile lane needs "
                    "`QA_MOBILE_RUN_ENABLED=true` in `.env`. Nothing was "
                    "downloaded, installed or created. Re-run without "
                    "`--apply` to see what it WOULD do."
                ),
                "content": None,
            }
        if apply:
            # Ownership is checked only on the apply path and only here, before
            # ensure_tree: a report changes nothing and must stay readable, but
            # a 2.2GB download into somebody else's directory must not start.
            owned = (paths.ownership() or {}).get("content") or {}
            if not owned.get("ok", True):
                _publish(
                    {
                        "phase": "error",
                        "pct": 0,
                        "bytes": 0,
                        "message": "refused: the mobile cache is not owned by you",
                        "error": str(owned.get("detail") or ""),
                    }
                )
                return {
                    "error": (
                        str(owned.get("detail") or "The mobile cache is not yours.")
                        + " "
                        + str(owned.get("fix") or "")
                    ).strip(),
                    "content": None,
                }
        paths.ensure_tree()
        built = plan()
        if built.get("error"):
            return built
        content = built.get("content") or {}
        steps = list(content.get("steps") or [])
        blocked = [s["id"] for s in steps if s.get("state") == BLOCKED_UNPINNED]
        pending = [s for s in steps if s.get("state") == PENDING and s.get("command")]
        executed: list[dict] = []
        would_run = [list(s["command"]) for s in pending]

        if not apply:
            _publish(
                {
                    "phase": "dry-run",
                    "pct": 0,
                    "bytes": 0,
                    "message": str(len(pending)) + " step(s) would run",
                    "error": None,
                    "pid": os.getpid(),
                    "started": time.time(),
                    "updated": time.time(),
                }
            )
            return {
                "error": None,
                "content": {
                    "executed": executed,
                    "would_run": would_run,
                    "blocked": blocked,
                    "steps": steps,
                },
            }

        if pending:
            cap = download_cap_bytes()
            if WORST_CASE_BYTES > cap:
                message = (
                    "Nothing was downloaded. Provisioning this machine could "
                    "fetch up to "
                    + "%.2f GB" % (WORST_CASE_BYTES / (1024**3))
                    + ", which exceeds QA_MOBILE_DOWNLOAD_MAX_GB="
                    + str(settings.qa_mobile_download_max_gb)
                    + " ("
                    + "%.2f GB" % (cap / (1024**3))
                    + "). Raise that limit in `.env`, or install Android "
                    "Studio so the lane reuses the SDK you already have."
                )
                _publish(
                    {
                        "phase": "error",
                        "pct": 0,
                        "bytes": 0,
                        "message": message,
                        "error": message,
                        "pid": os.getpid(),
                        "started": time.time(),
                        "updated": time.time(),
                    }
                )
                return {"error": message, "content": None}
            disk = (
                downloader.check_disk(WORST_CASE_BYTES, paths.sub("sdk")) or {}
            ).get("content") or {}
            if not disk.get("ok", False):
                message = str(disk.get("detail") or "insufficient disk space")
                _publish(
                    {
                        "phase": "error",
                        "pct": 0,
                        "bytes": 0,
                        "message": message,
                        "error": message,
                        "pid": os.getpid(),
                        "started": time.time(),
                        "updated": time.time(),
                    }
                )
                return {"error": message, "content": None}

        started = time.time()
        total = len(pending) or 1
        for index, step in enumerate(pending):
            _publish(
                {
                    "phase": str(step.get("id")),
                    "pct": int(index * 100 / total),
                    "bytes": 0,
                    "message": str(step.get("title")),
                    "error": None,
                    "pid": os.getpid(),
                    "started": started,
                    "updated": time.time(),
                }
            )
            command = list(step["command"])
            stdin_text = "y\n" * 64 if step.get("id") == "licenses" else None
            rc, out, err = _run_command(command, stdin_text)
            executed.append({"id": step.get("id"), "command": command, "rc": rc})
            if rc != 0:
                message = (
                    str(step.get("title"))
                    + " failed (rc="
                    + str(rc)
                    + "): "
                    + (err.strip() or out.strip() or "no output")[:400]
                )
                _publish(
                    {
                        "phase": "error",
                        "pct": int(index * 100 / total),
                        "bytes": 0,
                        "message": message,
                        "error": message,
                        "pid": os.getpid(),
                        "started": started,
                        "updated": time.time(),
                    }
                )
                return {
                    "error": message,
                    "content": {
                        "executed": executed,
                        "would_run": would_run,
                        "blocked": blocked,
                        "steps": steps,
                    },
                }
        _publish(
            {
                "phase": "done",
                "pct": 100,
                "bytes": 0,
                "message": str(len(executed)) + " step(s) completed",
                "error": None,
                "pid": os.getpid(),
                "started": started,
                "updated": time.time(),
            }
        )
        return {
            "error": None,
            "content": {
                "executed": executed,
                "would_run": would_run,
                "blocked": blocked,
                "steps": steps,
            },
        }
    except Exception as exc:
        logger.exception("mobile.provisioner.run failed")
        return {"error": str(exc), "content": None}


def _run_command(command: list[str], stdin_text: str | None) -> tuple[int, str, str]:
    """Run one provisioning command, feeding *stdin_text* when given.

    ``sdkmanager --licenses`` is interactive and reads ``y`` per licence from
    stdin. The acceptances travel on STDIN rather than as arguments for the
    same reason the IME's secret payload does: argv is world-readable.
    """
    if stdin_text is None:
        return _run_sync(command)
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell
            command,
            input=stdin_text.encode("utf-8"),
            capture_output=True,
            timeout=STEP_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return 127, "", "not found: " + (command[0] if command else "")
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as exc:
        return 126, "", str(exc)
    return (
        int(proc.returncode or 0),
        (proc.stdout or b"").decode(errors="replace"),
        (proc.stderr or b"").decode(errors="replace"),
    )


def start_detached() -> dict:
    """Launch ``-m tools.mobile.provisioner --apply`` as a detached process.

    Returns ``{"error", "content": {"pid", "progress"}}``. The caller returns
    immediately and polls the progress file; nothing about provisioning happens
    inside the MCP process.
    """
    try:
        if not settings.qa_mobile_run_enabled:
            return {
                "error": (
                    "Nothing was provisioned. The mobile lane needs "
                    "`QA_MOBILE_RUN_ENABLED=true` in `.env` and an MCP server "
                    "restart."
                ),
                "content": None,
            }
        owned = (paths.ownership() or {}).get("content") or {}
        if not owned.get("ok", True):
            # The same refusal as run(), at the second entry point, because a
            # guard on one of two doors is not a guard: this one is reached from
            # the MCP handler and run() from `python -m`.
            return {
                "error": (
                    str(owned.get("detail") or "The mobile cache is not yours.")
                    + " "
                    + str(owned.get("fix") or "")
                ).strip(),
                "content": None,
            }
        paths.ensure_tree()
        command = [sys.executable, "-m", "tools.mobile.provisioner", "--apply"]
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": str(_repo_root()),
        }
        kwargs.update(platform_info.detach_kwargs())
        pid = _spawn(command, **kwargs)
        _publish(
            {
                "phase": "starting",
                "pct": 0,
                "bytes": 0,
                "message": "provisioning started",
                "error": None,
                "pid": pid,
                "started": time.time(),
                "updated": time.time(),
            }
        )
        return {
            "error": None,
            "content": {"pid": pid, "progress": str(_progress_path())},
        }
    except Exception as exc:
        logger.exception("mobile.provisioner.start_detached failed")
        return {"error": str(exc), "content": None}


def render_plan(content: dict) -> str:
    """One line per step, for the ``--dry-run`` output and the status handler."""
    lines: list[str] = []
    host = dict(content.get("host") or {})
    sdk = dict(content.get("sdk") or {})
    lines.append(
        "host: "
        + str(host.get("os"))
        + "/"
        + str(host.get("arch"))
        + "  image abi: "
        + str(host.get("image_abi"))
    )
    lines.append(
        "sdk: "
        + (str(sdk.get("sdk_root")) or "(none found)")
        + "  source: "
        + (str(sdk.get("source")) or "-")
    )
    for step in content.get("steps") or []:
        lines.append(
            "  ["
            + str(step.get("state"))
            + "] "
            + str(step.get("title"))
            + " -- "
            + str(step.get("detail"))
        )
        if step.get("command"):
            lines.append("        would run: " + " ".join(step["command"]))
        if step.get("fix"):
            lines.append("        fix: " + str(step["fix"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m tools.mobile.provisioner``."""
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="tools.mobile.provisioner")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan for this machine and change nothing",
    )
    group.add_argument("--apply", action="store_true", help="execute the pending steps")
    args = parser.parse_args(argv)
    result = run(apply=bool(args.apply))
    content = result.get("content") or {}
    if content:
        logger.info("\n%s", render_plan(content))
    if result.get("error"):
        logger.error("%s", result["error"])
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
